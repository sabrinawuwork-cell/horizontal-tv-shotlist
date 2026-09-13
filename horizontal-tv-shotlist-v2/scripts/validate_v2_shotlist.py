#!/usr/bin/env python3
"""Validate V2 Stage 1 format, pinned inputs and two-way dialogue coverage.

The continuity notes are review evidence, not an automatic geometry proof.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

from shotlist_common import parse_frontmatter, read_text
from validate_shotlist import validate_shotlist_text, _scene_notes, _authorization_set
from v2_delivery_checks import check_digest, dialogue_errors, main_rows, source_file
from v2_project_inputs import project_inputs, optional_input
from content_contract import select_speech, speech_by_shot, quoted_texts


def speaker_line_dialogue(lines: list[str], speaker: str, speakers: list[str]) -> str:
    """Extract an explicitly bound source label; never infer the shot's speaker."""
    def label_pattern(name: str) -> str:
        # Whitespace within a printed name is layout, including 师  爷.
        return r'[ \t\u3000]*'.join(re.escape(c) for c in name if not c.isspace())

    prefix = re.match(r'^[ \t\u3000]*' + label_pattern(speaker) + r'[ \t\u3000]*[:：]', lines[0])
    if prefix is None:
        raise ValueError(f'首行未匹配声明角色 {speaker} 的角色名与冒号')
    body = lines[0][prefix.end():] + ''.join(lines[1:])
    # Only declared identities count as labels. Quoted labels belong to speech.
    label = re.compile(r'(?:' + '|'.join(label_pattern(s) for s in speakers) + r')[ \t\u3000]*[:：]')
    closing: list[str] = []
    pairs = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"'}
    for index, char in enumerate(body):
        if closing and char == closing[-1]:
            closing.pop()
        elif char in pairs:
            closing.append(pairs[char])
        elif not closing and label.match(body, index) and (index == 0 or body[index-1].isspace() or body[index-1] in '。！？!?；;'):
            raise ValueError('一个 source_ranges 区间包含多次角色标签，请按发言拆分区间')
    dialogue = ' '.join(line.strip() for line in body.splitlines() if line.strip())
    if not dialogue:
        raise ValueError('角色标签后对白为空')
    return dialogue


def declared_source_speech(payload: object, source_text: str, errors: list[str]) -> list[str] | None:
    """Use the author's declared speech positions, never scan a whole script."""
    if not isinstance(payload, dict):
        errors.append('对白清单需要对象格式及明确来源定位；由代理保留dialogue数组并补source_speech或source_ranges')
        return None
    if 'source_speech' in payload and 'source_ranges' in payload:
        errors.append('source_speech与source_ranges选择一种来源定位方式，不得同时声明')
        return None
    if 'source_speech' in payload:
        values, _spans, issues = select_speech(source_text, payload['source_speech'], '剧本发声来源')
        errors.extend(issues)
        return values if not issues else None
    ranges = payload.get('source_ranges')
    if not isinstance(ranges, list) or not ranges:
        errors.append('对白清单须声明source_speech或非空source_ranges，不再凭整份剧本的引号识别台词；无对白用source_speech: []')
        return None
    source_format = payload.get('source_format', 'quoted')
    if source_format not in {'quoted', 'lines', 'speaker_lines'}:
        errors.append('source_format只支持quoted、lines或speaker_lines')
        return None
    speakers = payload.get('source_speakers')
    if source_format == 'speaker_lines' and (
            not isinstance(speakers, list) or len(speakers) != len(ranges)
            or any(not isinstance(s, str) or not s.strip() or any(c in s for c in ':：\n\r') for s in speakers)):
        errors.append('speaker_lines须有与source_ranges逐段对应的非空source_speakers')
        return None
    lines = source_text.splitlines(keepends=True)
    result = []
    previous_end = 0
    for index, pair in enumerate(ranges):
        if (not isinstance(pair, list) or len(pair) != 2 or any(type(n) is not int for n in pair)
                or not previous_end < pair[0] <= pair[1] <= len(lines)):
            errors.append('source_ranges必须为按原文顺序、无重叠的一基行号闭区间')
            continue
        block = lines[pair[0]-1:pair[1]]
        try:
            if source_format == 'speaker_lines':
                result.append(speaker_line_dialogue(block, speakers[index], speakers))
            elif source_format == 'lines':
                result.append(' '.join(line.strip() for line in block if line.strip()))
            else:
                result.extend(quoted_texts(''.join(block)))
        except ValueError as exc:
            errors.append(f'已声明对白范围第{index+1}段：{exc}')
        previous_end = pair[1]
    return result


def validate_file(path: Path, *, require_plan: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        text = read_text(path)
        errors, _ = validate_shotlist_text(text, asset_root=path.parent)
        front = parse_frontmatter(text).values
        input_errors, _policy = project_inputs(path)
        errors.extend(input_errors)
        source = source_file(path, front.get('source', ''), 'Stage 1 剧本', errors)
        if source:
            check_digest(source, front.get('source_sha256', ''), 'Stage 1 剧本', errors)
        if require_plan or front.get('director_plan') or front.get('director_plan_sha256'):
            plan = source_file(path, front.get('director_plan', ''), 'Stage 1 前置导演方案', errors)
            if plan:
                check_digest(plan, front.get('director_plan_sha256', ''), 'Stage 1 导演方案', errors)
                plan_text = read_text(plan)
                parsed_plan = parse_frontmatter(plan_text)
                errors.extend(parsed_plan.errors)
                plan_front = parsed_plan.values
                plan_input_errors, _ = project_inputs(plan)
                errors.extend(plan_input_errors)
                for field in ('scene_facts', 'input_manifest'):
                    if field in plan_front or field + '_sha256' in plan_front:
                        plan_input = optional_input(plan, plan_front, field, errors)
                        stage1_input = optional_input(path, front, field, errors)
                        if plan_input is not None and stage1_input != plan_input:
                            errors.append(f'导演方案与 Stage 1 的 {field} 绑定不是同一文件')
                if plan_front.get('artifact') != 'horizontal-tv-director-plan' or plan_front.get('status') not in {'draft', 'approved'}:
                    errors.append('导演方案必须使用独立 artifact 和 draft/approved 状态')
                if front.get('status') == 'approved' and plan_front.get('status') != 'approved':
                    errors.append('批准的 Stage 1 必须引用同时确认的 approved 导演方案')
                plan_source = source_file(plan, plan_front.get('source', ''), '导演方案剧本', errors)
                if plan_source and source:
                    if plan_source != source:
                        errors.append('导演方案与 Stage 1 的权威剧本不是同一文件')
                    check_digest(source, plan_front.get('source_sha256', ''), '导演方案剧本', errors)
                if json.loads(plan_front.get('scene_images_sha256', '{}')) != json.loads(front.get('scene_images_sha256', '{}')):
                    errors.append('导演方案与 Stage 1 的场景图绑定不一致')
                if not '\n'.join(plan_text.splitlines()[parsed_plan.body_start_line - 1:]).strip():
                    errors.append('导演方案正文不能为空')
        image_hashes = json.loads(front.get('scene_images_sha256', '{}'))
        scenes = {row[0] for row in main_rows(text)}
        scene_notes = _scene_notes(text)
        authorized = _authorization_set(front.get('scene_image_authorizations', ''))
        pending_images = {scene for scene in scenes if front.get('status') == 'draft'
                          and scene.upper() in authorized and '待补' in scene_notes.get(scene, {}).get('场景图', '')}
        if not isinstance(image_hashes, dict) or set(image_hashes) != scenes - pending_images:
            errors.append('scene_images_sha256 必须按场次完整记录已有场景图摘要；仅已授权的无图draft可以暂缺')
            image_hashes = {}
        for scene, notes_by_scene in scene_notes.items():
            if scene not in scenes or scene in pending_images:
                continue
            image = source_file(path, notes_by_scene.get('场景图', ''), f'场次{scene}场景图', errors)
            if image:
                check_digest(image, image_hashes.get(scene, ''), f'场次{scene}场景图', errors)
        notes = source_file(path, front.get('continuity_notes', ''), 'Stage 1 连续性复核记录', errors)
        if notes:
            check_digest(notes, front.get('continuity_notes_sha256', ''), 'Stage 1 连续性复核记录', errors)
            if not read_text(notes).strip():
                errors.append('连续性复核记录不得为空')
        manifest = source_file(path, front.get('dialogue_manifest', ''), 'Stage 1 对白清单', errors)
        if manifest:
            check_digest(manifest, front.get('dialogue_manifest_sha256', ''), 'Stage 1 对白清单', errors)
            payload = json.loads(read_text(manifest))
            expected = payload.get('dialogue') if isinstance(payload, dict) else payload
            if not isinstance(expected, list) or any(not isinstance(line, str) or not line for line in expected):
                errors.append('对白清单必须是有序字符串数组；无对白使用空数组')
            else:
                if source:
                    source_dialogue = declared_source_speech(payload, read_text(source), errors)
                    if source_dialogue is not None:
                        errors.extend(dialogue_errors(source_dialogue, expected, '剧本到对白清单'))
                rows = main_rows(text)
                declared = payload.get('stage1_speech') if isinstance(payload, dict) else None
                speech, speech_errors = speech_by_shot(rows, declared)
                errors.extend(speech_errors)
                if not speech_errors:
                    actual = [value for row in rows for value in speech[row[1].upper()]]
                    errors.extend(dialogue_errors(expected, actual, 'Stage 1已声明发声', allow_split=True))
        from shot_revision import numbering_errors
        revision_errors, revision = numbering_errors(path, text)
        errors.extend(revision_errors)
        if revision is not None and revision['mode'] == 'local':
            # Historical IDs can survive deletion of their original base or an
            # explicitly anchored insertion before it. All syntax/content and
            # COV checks remain inherited; the actual old/new order was proven.
            import re
            errors = [error for error in errors if not (
                re.fullmatch(r'第 \d+ 行：插镜 [0-9A-Z-]+ 缺少基础镜号', error) or
                re.fullmatch(r'第 \d+ 行：场次 [0-9-]+ 镜号顺序倒退到 [0-9A-Z-]+', error))]
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        errors.append(f'V2 Stage 1 输入无法校验：{exc}')
    return list(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--require-plan', action='store_true', help='新生成 Stage 1 必须绑定前置导演方案；不验证方案创作质量或文件生成先后')
    args = parser.parse_args()
    errors = validate_file(args.path.resolve(), require_plan=args.require_plan)
    for error in errors:
        print(f'ERROR: {error}')
    if not errors:
        print('OK: V2 Stage 1 格式、输入绑定与对白内容检查通过；方案成立性、生成先后、空间和说话人仍需实际复核')
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
