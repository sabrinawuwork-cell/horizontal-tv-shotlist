"""V2 authority checks shared by the three public validation entry points.

Reuse the inherited parsers/format contract without invoking the retired draft
workflow. Physical continuity and speaker attribution still require review.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from shotlist_common import HEADERS, find_tables, is_coverage_table, parse_duration, parse_frontmatter, read_text
from validate_prompt_mapping import expected_storyboard, normalized_breaks
from content_contract import canonical_prompt_blocks, focal_lengths, select_speech, speech_by_shot, visual_blocks

QUOTES = re.compile(r'“([^”]*)”|‘([^’]*)’|"([^"\n]*)"')
PROMPT_TAIL = re.compile(r'无\s*配乐[\s,，、;；。]*无\s*字幕')
PROMPT_TAIL_END = re.compile(PROMPT_TAIL.pattern + r'[\s。.!！]*\Z')


def strip_prompt_tail(text: str) -> str:
    """Remove only the final no-music/no-subtitles restriction, not dialogue.

    Ordinary spacing and punctuation are formatting differences. Do not infer
    other wording, change source files, or normalize the rest of the prompt.
    """
    return PROMPT_TAIL_END.sub('', text).rstrip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_file(owner: Path, value: str, label: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value or value.lower() == 'none':
        errors.append(f'{label}缺少实际来源文件')
        return None
    path = (owner.parent / value).resolve()
    if not path.is_file():
        errors.append(f'{label}文件不存在：{value}')
        return None
    return path


def check_digest(path: Path, declared: str, label: str, errors: list[str]) -> None:
    if not isinstance(declared, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', declared) or declared.lower() != digest(path):
        errors.append(f'{label}摘要与当前文件不一致')


def main_rows(text: str) -> list[tuple[str, ...]]:
    return [row for table in find_tables(text) if table.headers == HEADERS and not is_coverage_table(table)
            for row in table.rows if len(row) == len(HEADERS)]


def dialogue_errors(expected: list[str], actual: list[str], label: str, *, allow_split: bool = False, authority: str = '') -> list[str]:
    if expected == actual or (allow_split and ''.join(expected) == ''.join(actual)):
        return []
    missing = list((Counter(expected) - Counter(actual)).elements())
    extra = list((Counter(actual) - Counter(expected)).elements())
    errors = []
    if missing:
        errors.append(f'{label}遗漏或改写{authority}原始台词：' + ' / '.join(missing))
    if extra:
        errors.append(f'{label}含新增、重复或旧版台词：' + ' / '.join(extra))
    if not errors:
        errors.append(f'{label}原始台词顺序改变')
    return errors


def blocks(prompt: str) -> list[str]:
    return canonical_prompt_blocks(prompt)


def contains_sound_unit(sound: str, card: str) -> bool:
    unit = card.split('：', 1)[-1].strip()
    if not unit:
        return False
    content = sound.removeprefix('声音：').strip()
    # A card must start at a sentence/section boundary, not inside its negation.
    end = '' if unit.endswith(('。', '；', ';', '.', '!', '?', '！', '？')) else r'(?=$|\s|[。；;\n])'
    return bool(re.search(r'(?:^|[。；;\n])\s*' + re.escape(unit) + end, content))


def stage2_sources(path: Path, expected_stage1: Path | None = None) -> tuple[list[str], Path | None]:
    errors: list[str] = []
    fm = parse_frontmatter(read_text(path))
    errors.extend(fm.errors)
    front = fm.values
    source = source_file(path, front.get('source_stage1', ''), 'Stage 2 的 Stage 1', errors)
    if source is None:
        return errors, None
    if expected_stage1 is not None and source != expected_stage1.resolve():
        errors.append('Stage 2 与 Stage 3 不是同一份 Stage 1 来源')
    upstream = parse_frontmatter(read_text(source)).values
    if upstream.get('artifact') != 'horizontal-tv-shotlist' or upstream.get('status') != 'approved':
        errors.append('Stage 2 的 Stage 1 来源必须是 approved 分镜')
    for field in ('project', 'episode'):
        if front.get(field) != upstream.get(field):
            errors.append(f'Stage 2 {field} 与 Stage 1 不一致')
    check_digest(source, front.get('stage1_sha256', ''), 'Stage 2 的 Stage 1', errors)
    scenes = {row[0] for row in main_rows(read_text(source))}
    if front.get('scene') not in scenes:
        errors.append('Stage 2 场次不存在于 Stage 1 主序列')
    image = source_file(path, front.get('scene_image', ''), 'Stage 2 场景图', errors)
    if image:
        from validate_shotlist import _scene_notes
        notes = _scene_notes(read_text(source)).get(front.get('scene', ''), {})
        declared = notes.get('场景图', '')
        if not declared or (source.parent / declared).resolve() != image:
            errors.append('Stage 2 场景图与本场 Stage 1 不一致')
    return errors, source


def stage3_authority(path: Path, rows: list[tuple[str, ...]], stage1: Path | None) -> tuple[list[str], dict]:
    errors: list[str] = []
    fm = parse_frontmatter(read_text(path))
    errors.extend(fm.errors)
    front = fm.values
    declared = source_file(path, front.get('source_stage1', ''), 'Stage 3 的 Stage 1', errors)
    if stage1 is not None and declared is not None and stage1.resolve() != declared:
        errors.append('--stage1 与 Stage 3 声明的来源不是同一文件')
    stage1 = declared
    if stage1 is None:
        return errors, {}
    text1 = read_text(stage1)
    from validate_v2_shotlist import validate_file as validate_stage1
    errors.extend(validate_stage1(stage1))
    from v2_project_inputs import project_inputs
    policy_errors, policy = project_inputs(stage1)
    errors.extend(policy_errors)
    source_front = parse_frontmatter(text1).values
    if source_front.get('status') != 'approved' or source_front.get('artifact') != 'horizontal-tv-shotlist':
        errors.append('Stage 3 的 Stage 1 来源必须是 approved 分镜')
    for field in ('project', 'episode'):
        if front.get(field) != source_front.get(field):
            errors.append(f'Stage 3 {field} 与 Stage 1 不一致')
    check_digest(stage1, front.get('stage1_sha256', ''), 'Stage 3 的 Stage 1', errors)
    expected = main_rows(text1)
    if [r[0].upper() for r in rows] != [r[1].upper() for r in expected]:
        errors.append('Stage 3 镜号必须与 Stage 1 主序列完整、唯一且顺序一致')

    # Local imports keep the formatting-only helpers free of circular imports.
    from validate_v2_stage2 import validate_text
    from validate_stage2_fixed_prompts import _cards
    names = [x.strip() for x in re.split('[,，]', front.get('source_stage2', '')) if x.strip()]
    hashes = [x.strip() for x in re.split('[,，]', front.get('stage2_sha256', '')) if x.strip()]
    if not names or len(names) != len(hashes):
        errors.append('Stage 3 必须按顺序列出全部 Stage 2 文件及对应摘要')
    cards_by_scene: dict[str, dict[str, str]] = {}
    for index, name in enumerate(names):
        source2 = source_file(path, name, 'Stage 3 的 Stage 2', errors)
        if source2 is None:
            continue
        text2 = read_text(source2)
        errors.extend(validate_text(text2, policy=policy))
        binding_errors, _ = stage2_sources(source2, stage1)
        errors.extend(binding_errors)
        check_digest(source2, hashes[index] if index < len(hashes) else '', 'Stage 3 的 Stage 2', errors)
        f2 = parse_frontmatter(text2).values
        scene = f2.get('scene', '')
        if f2.get('status') != 'approved':
            errors.append(f'场次 {scene} 的 Stage 2 尚未 approved')
        if scene in cards_by_scene:
            errors.append(f'场次 {scene} 重复提供 Stage 2')
        cards, card_errors = _cards(text2)
        errors.extend(card_errors)
        cards_by_scene[scene] = {name: normalized_breaks(content) for name, content, _ in cards}
    if set(cards_by_scene) != {r[0] for r in expected}:
        errors.append('Stage 2 必须覆盖全部且仅当前 Stage 1 场次')
    bindings = {}
    binding_path = source_file(path, front.get('stage3_bindings', ''), 'Stage 3 编译记录', errors)
    if binding_path:
        check_digest(binding_path, front.get('stage3_bindings_sha256', ''), 'Stage 3 编译记录', errors)
        payload = json.loads(read_text(binding_path))
        if not isinstance(payload, dict) or not isinstance(payload.get('shots'), dict):
            errors.append('Stage 3 编译记录必须包含 shots 对象')
        else:
            check_digest(stage1, payload.get('source_stage1_sha256', ''), '编译记录的 Stage 1', errors)
            bindings = payload['shots']
    source_map = {r[1].upper(): r for r in expected}
    if set(bindings) != set(source_map):
        errors.append('Stage 3 编译记录必须覆盖全部且仅当前主序列镜号')
    exceptions = {}
    for shot, binding in bindings.items():
        if shot not in source_map or not isinstance(binding, dict):
            errors.append(f'编译记录 {shot} 不属于当前主序列或格式错误')
            continue
        selected = binding.get('fixed_cards')
        if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
            errors.append(f'编译记录 {shot} 必须列出 fixed_cards 数组；无卡使用空数组')
        elif len(set(selected)) != len(selected) or any(name not in cards_by_scene.get(source_map[shot][0], {}) for name in selected):
            errors.append(f'编译记录 {shot} 固定卡重复或不属于本场 Stage 2')
        entries = binding.get('exceptions', {})
        if not isinstance(entries, dict):
            errors.append(f'单镜例外 {shot} 格式错误')
            continue
        exceptions[shot] = entries
        for category, entry in entries.items():
            if category not in {'光影', '背景', '环境', '群演', '外观'} or not isinstance(entry, dict):
                errors.append(f'单镜例外 {shot} 的类别错误')
                continue
            evidence = entry.get('stage1_evidence')
            if not entry.get('basis') or not isinstance(evidence, str) or not evidence or evidence not in source_map[shot][4]:
                errors.append(f'单镜例外 {shot} 缺少理由及 Stage 1 可追溯原文')
            if not isinstance(entry.get('text'), str) or not entry['text'].startswith(category + '：'):
                errors.append(f'单镜例外 {shot} 缺少带正确类别的完整正文')
    speech = {}
    manifest_path = source_file(stage1, source_front.get('dialogue_manifest', ''), 'Stage1对白清单', errors)
    if manifest_path is not None:
        dialogue_payload = json.loads(read_text(manifest_path))
        declaration = dialogue_payload.get('stage1_speech') if isinstance(dialogue_payload, dict) else None
        speech, speech_errors = speech_by_shot(expected, declaration)
        errors.extend(speech_errors)
    sources = source_map | {'_cards': cards_by_scene, '_exceptions': exceptions,
                            '_bindings': bindings, '_speech': speech}

    from shot_revision import numbering_errors
    revision_errors, revision = numbering_errors(stage1, text1)
    errors.extend(revision_errors)
    if revision is not None:
        sources['_shot_revision'] = revision
    from generation_tasks import load_generation_plan
    plan_errors, plan = load_generation_plan(path, stage1, rows, sources)
    errors.extend(plan_errors)
    if plan is not None:
        sources['_generation_plan'] = plan
    return errors, sources


def mapping_errors(rows: list[tuple[str, ...]], sources: dict) -> list[str]:
    errors: list[str] = []
    # Return review leads alongside sources, never as hard mapping failures.
    # Public validation/export entry points surface these to the existing review.
    sources['_content_warnings'] = []
    elapsed = Decimal(0)
    previous_segment = 0
    previous_scene = None
    totals: dict[int, Decimal] = defaultdict(Decimal)
    plan = sources.get('_generation_plan')
    for row in rows:
        shot, segment_text, duration_text, storyboard, prompt = row[:5]
        source = sources.get(shot.upper())
        try:
            duration = parse_duration(duration_text)
        except ValueError as exc:
            errors.append(f'镜头{shot}：{exc}')
            continue
        if not 0 < duration <= 30 or duration * 2 != (duration * 2).to_integral():
            errors.append(f'镜头{shot}：时长必须为正的整数或半秒且不超过30秒')
        match = re.fullmatch(r'片段([1-9]\d*)', segment_text)
        if not match:
            errors.append(f'镜头{shot}：片段必须使用片段1格式')
            continue
        segment = int(match[1])
        if plan is None and segment not in ({1} if previous_segment == 0 else {previous_segment, previous_segment + 1}):
            errors.append(f'镜头{shot}：片段编号必须从1连续递进，不得倒退或跳号')
        if segment != previous_segment:
            elapsed = Decimal(0)
        time_match = re.match(r'^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)秒。', prompt)
        expected_time = (elapsed, elapsed + duration)
        if plan is not None:
            clip = plan['clips'].get(shot)
            if clip is None or clip['task'] != segment_text:
                errors.append(f'镜头{shot}：片段引用与 generation_plan 不一致')
            else:
                expected_time = (clip['in'], clip['out'])
        if not time_match or (Decimal(time_match[1]), Decimal(time_match[2])) != expected_time:
            errors.append(f'镜头{shot}：提示词累计时间与片段及镜头时长不一致')
        elapsed += duration
        totals[segment] += duration
        if source:
            scene, _, source_duration, camera, picture, sound, _ = source
            if previous_scene is not None and scene != previous_scene and segment == previous_segment:
                errors.append(f'镜头{shot}：换场必须开启新片段')
            previous_scene = scene
            if duration != parse_duration(source_duration):
                errors.append(f'镜头{shot}：时长与 Stage 1 不一致')
            if normalized_breaks(storyboard) != normalized_breaks(expected_storyboard(camera, picture, sound)):
                errors.append(f'镜头{shot}：分镜内容必须逐字合并 Stage 1 摄影机、画面内容和音效')
            parts = blocks(prompt)
            action = next((b for b in parts if b.startswith('动作/表情：')), '')
            binding = sources.get('_bindings', {}).get(shot.upper(), {})
            declaration = binding.get('speech') if isinstance(binding, dict) else None
            action_body = action.removeprefix('动作/表情：')
            actual_speech, _spans, speech_errors = select_speech(action_body, declaration, f'Stage3镜头{shot}')
            errors.extend(speech_errors)
            if not speech_errors and shot.upper() in sources.get('_speech', {}):
                errors.extend(dialogue_errors(sources['_speech'][shot.upper()], actual_speech,
                                             f'镜头{shot}已声明发声', allow_split=True, authority='Stage1'))
            first = parts[0] if parts else ''
            if focal_lengths(camera) != focal_lengths(first):
                errors.append(f'镜头{shot}：提示词毫米焦段数值或顺序与Stage1不一致')
            # Scale, camera position/shoulder, path and motion meaning require
            # source-grounded review. Equivalent words are not changed facts.
            movement = r'固定|手持|稳定器|推近|拉远|横移|摇镜|上摇|下摇|跟随|升起|下降|环绕|变焦'
            if set(re.findall(movement, camera)) != set(re.findall(movement, first)):
                sources['_content_warnings'].append({
                    'code': 'camera_movement_words_differ', 'shot': shot,
                    'source_camera': camera, 'prompt_camera': first,
                    'message': f'镜头{shot}：摄影机运动词集合不同（非阻塞，不等于拍法改变）。'
                               '请在现有内容自查中对照实际拍法：等义且专业准确则保留原句，'
                               '实际运动变化或遗漏则修正；集合相同也不证明方向、路径或次序正确。',
                })
            available_cards = sources.get('_cards', {}).get(scene, {})
            binding = sources.get('_bindings', {}).get(shot.upper(), {})
            selected = binding.get('fixed_cards', []) if isinstance(binding, dict) else []
            selected = selected if isinstance(selected, list) else []
            approved_cards = [available_cards[name] for name in selected if isinstance(name, str) and name in available_cards]
            raw_exceptions = sources.get('_exceptions', {}).get(shot.upper(), {})
            exceptions = raw_exceptions if isinstance(raw_exceptions, dict) else {}
            exception_texts = [normalized_breaks(e['text']) for e in exceptions.values()
                               if isinstance(e, dict) and isinstance(e.get('text'), str)]
            expected_visuals = visual_blocks(approved_cards + exception_texts)
            actual_visuals = visual_blocks(parts)
            if actual_visuals != expected_visuals:
                errors.append(f'镜头{shot}：固定条件正文与已选卡片/单镜例外不一致，允许合并类别标签但不得改写或遗漏正文')
            sound_block = next((b for b in parts if b.startswith('声音：')), '')
            for card in approved_cards:
                if card.startswith(('声音：', '音色：', '音色状态：')) and not contains_sound_unit(sound_block, card):
                    errors.append(f'镜头{shot}：未完整展开编译记录中选定的固定声音/音色卡')
            for name, card in available_cards.items():
                if name not in selected and card.startswith(('声音：', '音色：', '音色状态：')) and contains_sound_unit(sound_block, card):
                    errors.append(f'镜头{shot}：混入未选择的固定声音/音色卡')
        previous_segment = segment
    for segment, total in totals.items():
        if total > 30:
            errors.append(f'片段{segment}累计{total}秒，超过30秒硬上限')
    return errors
