#!/usr/bin/env python3
"""Validate pinned generation plans and export complete, local task prompts.

This compiles approved files; it does not generate, upload, or extend video.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from shotlist_common import parse_duration, parse_frontmatter, read_text
from content_contract import visual_blocks
from v2_delivery_checks import QUOTES, blocks, check_digest, digest, source_file, strip_prompt_tail


VISUAL_LABELS = ('光影：', '背景：', '环境：', '群演：', '外观：')
OTHER_LABELS = ('画面：', '动作/表情：', '情感解析：', '声音：', '音色：', '音色状态：')
# Unicode letters include Chinese labels. ASCII word/local-part adjacency and
# domain punctuation identify ordinary email text, not a material mention.
# Chinese narration may directly precede a label, as in “参考@图片1”。
REFERENCE_LABEL = re.compile(r'(?<![A-Za-z0-9_.%+\-])@[^\W\d_]\w*(?: [1-9]\d*)?(?![\w.%+\-])')
TASK_REQUIREMENT = '全片要求：不添加背景配乐，不生成字幕。'
TAIL = '无配乐无字幕'


def _reference_pattern(labels=()) -> re.Pattern:
    """Read numbered mentions and declared names without requiring Chinese separators."""
    boundary = r'(?![A-Za-z0-9_.%+\-])'
    # Consume the whole number before considering shorter declared names:
    # @Image 10 must never bind to @Image 1 (or the bare @Image).
    numbered = r'@[^\W\d_]\w* [1-9]\d*' + boundary
    known = [re.escape(label) + boundary for label in sorted(labels, key=lambda s: (-len(s), s))]
    # Retain detection of undeclared custom tags and exclusion of email text.
    return re.compile(r'(?<![A-Za-z0-9_.%+\-])(?:' + '|'.join(
        [numbered, *known, REFERENCE_LABEL.pattern]) + ')')


def _file_state(path: Path) -> tuple:
    stat = path.stat()
    link = path.lstat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
            link.st_dev, link.st_ino, link.st_mtime_ns, link.st_ctime_ns)


class _InputSnapshot:
    """A bounded export read-set, following only the existing input schemas.

    Public validators still check the real files. Their reads and the compiled
    text must stay within this unchanged read-set before any output is written.
    Recorded hashes always describe these initial bytes, never a late re-read.
    """
    def __init__(self, stage3: Path, *, kind: str = 'stage3'):
        self.entries: dict[Path, dict] = {}
        self.by_target: dict[Path, dict] = {}
        self.texts: dict[Path, str] = {}
        self.visited: set[tuple[Path, str]] = set()
        self.roles: dict[Path, set[str]] = {}
        self.stage3 = self._capture(stage3, kind)

    def _capture(self, path: Path, kind: str = 'asset') -> Path:
        logical = Path(os.path.abspath(path))
        if logical not in self.entries:
            if not logical.is_file():
                raise ValueError(f'导出输入文件不存在：{logical}')
            target = logical.resolve()
            before = _file_state(logical)
            sha = hashlib.sha256()
            with logical.open('rb') as stream:
                if kind == 'asset':
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        sha.update(chunk)
                else:
                    data = stream.read()
                    sha.update(data)
                    self.texts[target] = data.decode('utf-8-sig')
            if _file_state(logical) != before or logical.resolve() != target:
                raise ValueError(f'导出期间输入已变化：{logical}')
            entry = {'path': target, 'sha256': sha.hexdigest(), 'state': before}
            prior = self.by_target.get(target)
            if prior is not None and prior['sha256'] != entry['sha256']:
                raise ValueError(f'导出期间输入已变化：{logical}')
            self.entries[logical] = entry
            self.by_target[target] = entry
        target = self.entries[logical]['path']
        self.roles.setdefault(target, set()).add(kind)
        if kind == 'asset' or (target, kind) in self.visited:
            return target
        self.visited.add((target, kind))
        # A file first encountered as an opaque asset can later be a declared
        # document. Keep this read bound to the same initial bytes as well.
        if target not in self.texts:
            data = logical.read_bytes()
            if hashlib.sha256(data).hexdigest() != self.entries[logical]['sha256']:
                raise ValueError(f'导出期间输入已变化：{logical}')
            self.texts[target] = data.decode('utf-8-sig')
        text = self.texts[target]
        if kind == 'authority':
            artifact = parse_frontmatter(text).values.get('artifact')
            kind = 'stage1' if artifact == 'horizontal-tv-shotlist' else 'stage3'
            self.roles[target].add(kind)

        def follow(value, child_kind='asset'):
            if isinstance(value, str) and value.strip() and value.lower() != 'none':
                return self._capture(target.parent / value, child_kind)
            return None

        if kind in {'stage3', 'stage2', 'stage1', 'director_plan', 'revision_base', 'renumber_base'}:
            front = parse_frontmatter(text).values
            if kind in {'stage1', 'revision_base', 'renumber_base'}:
                follow(front.get('revision_record'), 'revision_record')
            if kind == 'renumber_base':
                follow(front.get('continuity_notes'), 'revision_notes')
                follow(front.get('dialogue_manifest'), 'revision_dialogue')
            if kind in {'stage3', 'stage2'}:
                follow(front.get('source_stage1'), 'stage1')
            if kind == 'stage3':
                for name in re.split('[,，]', front.get('source_stage2', '')):
                    follow(name.strip(), 'stage2')
                follow(front.get('generation_plan'), 'generation_plan')
                follow(front.get('stage3_bindings'))
            if kind == 'stage2':
                follow(front.get('scene_image'))
            if kind in {'stage1', 'director_plan'}:
                for field in ('source', 'dialogue_manifest', 'continuity_notes', 'scene_facts'):
                    follow(front.get(field))
                follow(front.get('input_manifest'), 'input_manifest')
                follow(front.get('director_plan'), 'director_plan')
                from validate_shotlist import _scene_notes
                for notes in _scene_notes(text).values():
                    image = notes.get('场景图', '')
                    if '待补' not in image:
                        follow(image)
        elif kind in {'input_manifest', 'generation_plan', 'revision_record'}:
            # Shape validity belongs to the public validator. Snapshot only
            # declared paths that can be read from the existing JSON schema.
            payload = json.loads(text)
            if isinstance(payload, dict):
                if kind == 'revision_record':
                    follow(payload.get('base_stage1'), 'renumber_base' if payload.get('mode') == 'renumber' else 'revision_base')
                if kind == 'input_manifest' and isinstance(payload.get('inputs'), list):
                    for item in payload['inputs']:
                        if isinstance(item, dict):
                            follow(item.get('path'))
                if kind == 'generation_plan' and isinstance(payload.get('tasks'), list):
                    for task in payload['tasks']:
                        if isinstance(task, dict) and isinstance(task.get('references'), list):
                            for reference in task['references']:
                                if isinstance(reference, dict):
                                    follow(reference.get('path'))
        return target

    def record(self, path: Path) -> dict:
        logical = Path(os.path.abspath(path))
        entry = self.entries.get(logical) or self.by_target.get(logical)
        if entry is None:
            raise ValueError(f'导出期间出现未包含在初始快照中的输入：{path}')
        return {'path': str(entry['path']), 'sha256': entry['sha256']}

    def verify_unchanged(self) -> None:
        for logical, entry in self.entries.items():
            try:
                if logical.resolve() != entry['path'] or _file_state(logical) != entry['state']:
                    raise ValueError(f'导出期间输入已变化：{logical}')
                current = hashlib.sha256()
                with logical.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        current.update(chunk)
                if current.hexdigest() != entry['sha256'] or _file_state(logical) != entry['state']:
                    raise ValueError(f'导出期间输入已变化：{logical}')
            except OSError as exc:
                raise ValueError(f'导出期间输入已变化或不可读取：{logical}') from exc


def _seconds(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def _references(task: dict, owner: Path, errors: list[str],
                file_digests: dict[Path, str] | None = None) -> list[dict]:
    file_digests = {} if file_digests is None else file_digests
    refs = task.get('references')
    if not isinstance(refs, list):
        errors.append(f"{task.get('id', '?')} references 必须为数组；没有素材使用空数组")
        return []
    result = []
    labels = set()
    for entry in refs:
        if not isinstance(entry, dict):
            errors.append('参考素材必须包含 label、path、sha256、use')
            continue
        label, raw_path, use = entry.get('label'), entry.get('path'), entry.get('use')
        if not isinstance(label, str) or not REFERENCE_LABEL.fullmatch(label):
            errors.append('参考素材 label 必须为单个明确的 @ 引用标签')
            continue
        if label.casefold() in labels:
            errors.append(f"{task.get('id', '?')} 参考标签重复：{label}")
        labels.add(label.casefold())
        if not isinstance(use, str) or not use.strip():
            errors.append(f'参考素材 {label} 缺少用途说明')
        if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
            errors.append(f'参考素材 {label} path 必须相对 generation_plan 文件')
            continue
        target = source_file(owner, raw_path, f'参考素材 {label}', errors)
        if target is not None:
            if target not in file_digests:
                file_digests[target] = digest(target)
            declared = entry.get('sha256')
            if not isinstance(declared, str) or declared.lower() != file_digests[target]:
                errors.append(f'参考素材 {label}摘要与当前文件不一致')
            result.append({'label': label, 'path': str(target), 'sha256': entry.get('sha256'), 'use': use})
    return result


def load_generation_plan(stage3: Path, stage1: Path, rows: list[tuple[str, ...]],
                         sources: dict) -> tuple[list[str], dict | None]:
    """Return a usable plan only after its files, clips, and evidence validate."""
    errors: list[str] = []
    front = parse_frontmatter(read_text(stage3)).values
    if 'generation_plan' not in front and 'generation_plan_sha256' not in front:
        return [], None
    if not front.get('generation_plan') or not front.get('generation_plan_sha256'):
        errors.append('generation_plan 与 generation_plan_sha256 必须成对声明')
    owner = source_file(stage3, front.get('generation_plan', ''), 'generation_plan', errors)
    if owner is None:
        return errors, None
    try:
        check_digest(owner, front.get('generation_plan_sha256', ''), 'generation_plan', errors)
        payload = json.loads(read_text(owner))
        if not isinstance(payload, dict) or type(payload.get('version')) is not int or payload.get('version') != 1:
            return errors + ['generation_plan 必须为 version: 1 的 JSON 对象'], None
        check_digest(stage1, payload.get('source_stage1_sha256', ''), 'generation_plan 的 Stage 1', errors)
        tasks = payload.get('tasks')
        if not isinstance(tasks, list) or not tasks:
            return errors + ['generation_plan tasks 必须是非空数组'], None
        ordered_shots = [shot for shot in sources if not shot.startswith('_')]
        position = {shot: index for index, shot in enumerate(ordered_shots)}
        shot_clips: dict[str, dict] = {}
        result = []
        first_positions = []
        file_digests: dict[Path, str] = {}
        for index, task in enumerate(tasks, 1):
            if not isinstance(task, dict):
                errors.append('generation_plan 每个 task 必须为对象')
                continue
            task_id = task.get('id')
            if task_id != f'片段{index}':
                errors.append('generation_plan 任务目录必须按片段1至片段N连续编号')
            references = _references(task, owner, errors, file_digests)
            clips = task.get('clips')
            if not isinstance(clips, list) or not clips:
                errors.append(f'{task_id} clips 必须为非空数组')
                continue
            elapsed = Decimal(0)
            indices = []
            scenes = set()
            normalized_clips = []
            for clip in clips:
                if not isinstance(clip, dict) or not isinstance(clip.get('shot'), str) or clip['shot'] not in position:
                    errors.append(f'{task_id} clip 必须引用当前 Stage 1 的真实镜号')
                    continue
                shot = clip['shot']
                if shot in shot_clips:
                    errors.append(f'generation_plan 镜头重复映射：{shot}')
                start, end = _seconds(clip.get('in')), _seconds(clip.get('out'))
                if start is None or end is None or end <= start:
                    errors.append(f'{task_id}/{shot} in/out 必须为递增、有限且非负的JSON数值')
                    continue
                if start != elapsed:
                    errors.append(f'{task_id}/{shot} 任务时码必须从0连续，不得重叠或留空洞')
                if end - start != parse_duration(sources[shot][2]):
                    errors.append(f'{task_id}/{shot} 素材时长与 Stage 1 镜头不一致')
                elapsed = end
                indices.append(position[shot])
                scenes.add(sources[shot][0])
                normalized_clip = {'shot': shot, 'in': start, 'out': end}
                normalized_clips.append(normalized_clip)
                shot_clips[shot] = normalized_clip | {'task': task_id}
            if indices != sorted(indices) or len(indices) != len(set(indices)):
                errors.append(f'{task_id} clips 必须保持 Stage 1 原相对顺序且不重复')
            if len(scenes) > 1:
                errors.append(f'{task_id} 不得跨 Stage 1 场次')
            if elapsed > 30:
                errors.append(f'{task_id} 单次任务超过30秒；本入口未实现延长或多次调用')
            if indices:
                first_positions.append(indices[0])
            non_adjacent = any(right != left + 1 for left, right in zip(indices, indices[1:]))
            basis = task.get('reuse_basis')
            if non_adjacent or basis is not None:
                evidence = basis.get('stage1_evidence') if isinstance(basis, dict) else None
                reason = basis.get('reason') if isinstance(basis, dict) else None
                if not isinstance(reason, str) or not reason.strip() or not isinstance(evidence, list) or not evidence:
                    errors.append(f'{task_id} 非相邻素材复用必须给出 reuse_basis 理由及 Stage 1 连接原文')
                elif indices:
                    relevant = [sources[shot] for shot in ordered_shots[min(indices):max(indices) + 1]]
                    corpus = [cell for row in relevant for cell in (row[4], row[6])]
                    if any(not isinstance(item, str) or not item.strip() or not any(item in cell for cell in corpus) for item in evidence):
                        errors.append(f'{task_id} reuse_basis 必须引用对应 Stage 1 画面或剪辑中的真实原文')
            result.append({'id': task_id, 'clips': normalized_clips, 'references': references,
                           'duration': elapsed, 'reuse_basis': basis})
        if first_positions != sorted(first_positions):
            errors.append('generation_plan 任务目录必须按成片中首次出现的顺序编号')
        if set(shot_clips) != set(ordered_shots):
            errors.append('generation_plan 必须完整且仅映射一次 Stage 1 全部镜头')
        task_by_id = {task['id']: task for task in result if isinstance(task['id'], str)}
        reference_checks = {}
        for task_id, task in task_by_id.items():
            labels = {ref['label'] for ref in task['references']}
            reference_checks[task_id] = (labels, _reference_pattern(labels))
        for row in rows:
            shot, task_id = row[:2]
            assigned = shot_clips.get(shot)
            if assigned is None or assigned['task'] != task_id:
                errors.append(f'镜头{shot} 的片段引用与 generation_plan 不一致')
            if '@' not in row[4] or task_id not in reference_checks:
                continue
            labels, pattern = reference_checks[task_id]
            used = set(pattern.findall(QUOTES.sub('', row[4])))
            if used - labels:
                errors.append(f'镜头{shot} 引用了未声明的素材标签：' + '、'.join(sorted(used - labels)))
        if errors:
            return errors, None
        return [], {'path': str(owner), 'sha256': digest(owner), 'tasks': result, 'clips': shot_clips}
    except (OSError, UnicodeError, ValueError, TypeError, InvalidOperation) as exc:
        return errors + [f'generation_plan 无法校验：{exc}'], None


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral() else float(value)


def _legacy_tasks(rows: list[tuple[str, ...]]) -> list[dict]:
    """Only called after public validation proves the continuous legacy order."""
    tasks = []
    reference_pattern = _reference_pattern()
    for row in rows:
        shot, task_id = row[:2]
        if not tasks or tasks[-1]['id'] != task_id:
            tasks.append({'id': task_id, 'clips': [], 'references': [], 'duration': Decimal(0)})
        task = tasks[-1]
        start = task['duration']
        task['duration'] += parse_duration(row[2])
        task['clips'].append({'shot': shot, 'in': start, 'out': task['duration']})
        if '@' in row[4] and reference_pattern.search(QUOTES.sub('', row[4])):
            raise ValueError(f'镜头{shot} 含参考标签；须先在绑定的 generation_plan 声明素材，不能凭空补引用')
    return tasks


def _common_visuals(clips: list[dict], row_map: dict, sources: dict) -> list[str]:
    if len(clips) < 2:
        return []
    common: list[str] | None = None
    for clip in clips:
        shot = clip['shot']
        source = sources[shot]
        card_map = sources['_cards'].get(source[0], {})
        selected = sources['_bindings'][shot]['fixed_cards']
        approved = set(visual_blocks([card_map[name] for name in selected]))
        eligible = [block for block in blocks(row_map[shot][4])
                    if block.startswith(VISUAL_LABELS) and block in approved
                    and not QUOTES.search(block) and not any(label in block for label in OTHER_LABELS)]
        common = eligible if common is None else [block for block in common if block in eligible]
    return common or []


def compile_delivery(stage3: Path, output_dir: Path, *, allow_voice_reference: bool = False) -> list[Path]:
    """Validate real upstream files, then write a fresh task package locally."""
    from validate_v2_prompts import checked_stage3, print_content_warnings
    snapshot = _InputSnapshot(stage3)
    stage3 = snapshot.stage3
    errors, rows, sources = checked_stage3(stage3, allow_voice_reference=allow_voice_reference)
    print_content_warnings(sources)
    if errors:
        raise ValueError('\n'.join(errors))
    text = snapshot.texts[stage3]
    plan = sources.get('_generation_plan')
    tasks = plan['tasks'] if plan else _legacy_tasks(rows)
    row_map = {row[0]: row for row in rows}
    files: dict[str, str] = {}
    catalogue = []
    reference_tasks = []
    clip_lookup = {}
    for index, task in enumerate(tasks, 1):
        common = _common_visuals(task['clips'], row_map, sources)
        sections = [TASK_REQUIREMENT, f"时长：{task['duration']}秒。"]
        if task['references']:
            sections.append('参考素材：\n' + '\n'.join(f"{ref['label']}：{ref['use']}" for ref in task['references']))
        if common:
            sections.append('全任务固定条件：\n\n' + '\n\n'.join(common))
        timeline = []
        for clip in task['clips']:
            parts = [block for block in blocks(row_map[clip['shot']][4]) if block not in common]
            # Normalize only terminal restriction punctuation, using the same
            # interpretation as validation. Preserve all other text and quotes.
            parts[-1] = strip_prompt_tail(parts[-1])
            timeline.append('\n\n'.join(block for block in parts if block))
            clip_lookup[clip['shot']] = {'task': task['id'], 'in': _number(clip['in']), 'out': _number(clip['out'])}
        sections.append('时间线：\n\n' + '\n\n'.join(timeline))
        sections.append(TAIL)
        filename = f'task-{index:03d}.txt'
        files[filename] = '\n\n'.join(sections) + '\n'
        catalogue.append({'id': task['id'], 'prompt_file': filename, 'duration': _number(task['duration']),
                          'shots': [clip['shot'] for clip in task['clips']]})
        reference_tasks.append({'id': task['id'], 'references': task['references']})
    timeline_time = Decimal(0)
    edit_clips = []
    for row in rows:
        end = timeline_time + parse_duration(row[2])
        edit_clips.append({'shot': row[0], **clip_lookup[row[0]],
                           'timeline_in': _number(timeline_time), 'timeline_out': _number(end)})
        timeline_time = end
    front = parse_frontmatter(text).values
    source1 = stage3.parent / front['source_stage1']
    source2 = [stage3.parent / value.strip() for value in re.split('[,，]', front['source_stage2'])]
    provenance = {'stage3': snapshot.record(stage3),
                  'stage1': snapshot.record(source1),
                  'stage2': [snapshot.record(path) for path in source2],
                  'inputs': [snapshot.record(path) | {'roles': sorted(snapshot.roles[path])}
                             for path in sorted(snapshot.by_target)]}
    if plan:
        provenance['generation_plan'] = snapshot.record(Path(plan['path']))
    files['references.json'] = json.dumps({'version': 1, 'tasks': reference_tasks}, ensure_ascii=False, indent=2) + '\n'
    edit_map = {'version': 1, 'sources': provenance, 'tasks': catalogue, 'clips': edit_clips}
    if sources.get('_content_warnings'):
        edit_map['content_warnings'] = sources['_content_warnings']
    revision = sources.get('_shot_revision')
    if revision is not None:
        edit_map['shot_revision'] = {'record': snapshot.record(Path(revision['path'])),
            'base_stage1': snapshot.record(Path(revision['base_stage1'])),
            'mode': revision['mode'], 'mapping': revision['mapping']}
    files['edit-map.json'] = json.dumps(edit_map, ensure_ascii=False, indent=2) + '\n'
    output_dir = output_dir.resolve()
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError('输出目录必须不存在或为空；请使用新的导出目录')
    snapshot.verify_unchanged()
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        path = output_dir / name
        path.write_text(content, encoding='utf-8')
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage3', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--allow-voice-reference', action='store_true')
    args = parser.parse_args()
    try:
        paths = compile_delivery(args.stage3, args.output_dir, allow_voice_reference=args.allow_voice_reference)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f'ERROR: {exc}')
        return 1
    print('OK: 已导出经来源校验的任务文本、素材引用和成片回剪映射；未生成或上传视频')
    for path in paths:
        print(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
