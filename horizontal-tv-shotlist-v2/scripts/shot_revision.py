#!/usr/bin/env python3
"""Bind local shot revisions to actual prior rows, or map a pure renumbering.

A revision record is evidence of the declared edit, not permission to rewrite
story facts or proof that a creative change is sound. No platform calls occur.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from shotlist_common import HEADERS, find_tables, is_coverage_table, parse_frontmatter, parse_shot_id, read_text
from v2_delivery_checks import check_digest, digest, main_rows, source_file


def row_digest(row) -> str:
    return hashlib.sha256(json.dumps(list(row), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _rows(text: str) -> list[tuple[str, ...]]:
    tables = [t for t in find_tables(text) if '镜号' in t.headers and not is_coverage_table(t)]
    if not tables or any(t.headers != HEADERS or any(len(r) != len(HEADERS) for r in t.rows) for t in tables):
        raise ValueError('修订旧新稿都必须有完整七列主序列，不能跳过坏行')
    rows = main_rows(text)
    ids = [r[1] for r in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError('修订旧新稿主序列为空或镜号重复')
    scenes = []
    for row in rows:
        shot = parse_shot_id(row[1])
        if shot is None or row[1] != row[1].upper() or shot.coverage is not None or shot.scene != row[0]:
            raise ValueError('修订旧新稿存在非法主镜号或场次前缀不一致')
        if not scenes or scenes[-1] != row[0]:
            if row[0] in scenes:
                raise ValueError('修订不得把同场主序列拆成不连续分组')
            scenes.append(row[0])
    return rows


def _replace_ids(value: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return value
    names = '|'.join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
    parts = re.split(r'(“[^”]*”|‘[^’]*’|\"[^\"\n]*\")', value)
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r'(?<![A-Za-z0-9-])(?:' + names + r')(?![A-Za-z0-9]|-\d)',
                              lambda match: mapping[match[0]], parts[index])
    return ''.join(parts)


def _mapping(old_rows, new_rows, pairs):
    old = {r[1]: (i + 1, r) for i, r in enumerate(old_rows)}
    new = {r[1]: (i + 1, r) for i, r in enumerate(new_rows)}
    return [{'old': a, 'new': b,
             'old_index': old[a][0] if a else None, 'new_index': new[b][0] if b else None,
             'old_row_sha256': row_digest(old[a][1]) if a else None,
             'new_row_sha256': row_digest(new[b][1]) if b else None} for a, b in pairs]


def build_mapping(old_text: str, new_text: str, mode: str, changes: list) -> list[dict]:
    """Derive the entire map from actual row order/content; never trust its labels."""
    if not isinstance(changes, list):
        raise ValueError('changes 必须为数组；renumber 使用空数组')
    old_rows, new_rows = _rows(old_text), _rows(new_text)
    old, new = {r[1]: r for r in old_rows}, {r[1]: r for r in new_rows}
    if mode == 'renumber':
        if changes:
            raise ValueError('renumber 仅做编号迁移，changes 必须为空；内容修改先走 local')
        from validate_v2_prompts import normalized_shot_sequence_errors
        errors = normalized_shot_sequence_errors(list(new))
        if errors:
            raise ValueError('；'.join(errors))
        if len(old_rows) != len(new_rows):
            raise ValueError('renumber 不得同时插删镜头')
        pairs = list(zip(old, new))
        ids = dict(pairs)
        for before, after in zip(old_rows, new_rows):
            expected = list(before)
            expected[1] = ids[before[1]]
            expected[6] = _replace_ids(before[6], ids)
            if tuple(expected) != after:
                raise ValueError('renumber 必须保持原行序与全部内容；只允许按完整映射更新镜号引用')
        return _mapping(old_rows, new_rows, pairs)
    if mode != 'local':
        raise ValueError('修订 mode 只支持 local 或 renumber')
    if not isinstance(changes, list):
        raise ValueError('changes 必须是明确的修改、插镜、删镜数组')
    entries = {}
    for entry in changes:
        if not isinstance(entry, dict):
            raise ValueError('changes 每项必须为对象')
        kind, shot = entry.get('kind'), entry.get('shot')
        fields = {'kind', 'shot', 'reason'} | ({'anchor', 'position'} if kind == 'insert' else set())
        if set(entry) != fields or kind not in {'modify', 'insert', 'delete'} or not isinstance(shot, str):
            raise ValueError('changes 只支持精确的 modify/delete 或带 anchor/position 的 insert')
        if not isinstance(entry['reason'], str) or not entry['reason'].strip() or shot in entries:
            raise ValueError('每个变更镜号只能声明一次并给出具体理由')
        entries[shot] = entry
    required = {shot: 'delete' for shot in old.keys() - new.keys()}
    required.update({shot: 'insert' for shot in new.keys() - old.keys()})
    required.update({shot: 'modify' for shot in old.keys() & new.keys() if old[shot] != new[shot]})
    if {shot: entry['kind'] for shot, entry in entries.items()} != required:
        raise ValueError('changes 必须且只能覆盖实际改动的镜头；未声明修改、虚假变更或改号不能绕过')
    deleted_rows = [old[shot] for shot in old.keys() - new.keys()]
    for shot in new.keys() - old.keys():
        if any(new[shot][:1] + new[shot][2:] == row[:1] + row[2:] for row in deleted_rows):
            raise ValueError('local 不得把仅换编号伪装为删镜加插镜；请使用 renumber 完整映射')
    before, after = {}, {}
    for shot, entry in entries.items():
        if entry['kind'] != 'insert':
            continue
        anchor, position = entry['anchor'], entry['position']
        if not isinstance(anchor, str) or anchor not in old or position not in {'before', 'after'}:
            raise ValueError('插镜必须定位到实际旧主镜及 before/after，锚点可以在本次明确删除')
        parsed, base = parse_shot_id(shot), parse_shot_id(anchor)
        if (parsed is None or parsed.insertion is None or parsed.insertion < 1 or
                (parsed.scene, parsed.letters) != (base.scene, base.letters)):
            raise ValueError('local 新镜必须使用所属旧锚点基础字母的正整数插镜后缀，不得改成另一基础镜号')
        (before if position == 'before' else after).setdefault(anchor, []).append(shot)
    order = []
    for shot in old:
        order.extend(before.get(shot, []))
        if shot in new:
            order.append(shot)
        order.extend(after.get(shot, []))
    if order != list(new):
        raise ValueError('local 必须保留既有镜号行序；插镜顺序须与声明的旧镜锚点一致')
    pairs = [(shot if shot in old else None, shot) for shot in new]
    pairs.extend((shot, None) for shot in old if shot not in new)
    return _mapping(old_rows, new_rows, pairs)


def _renumber_related(path: Path, base: Path, text: str, old_text: str, mapping: list[dict], errors: list[str]) -> None:
    """Protect typed shot references; renumbering cannot alter source decisions."""
    current, prior = parse_frontmatter(text).values, parse_frontmatter(old_text).values
    ids = {entry['old']: entry['new'] for entry in mapping}
    from validate_shotlist import _authorization_set
    for field in ('long_shot_authorizations', 'active_cast_authorizations'):
        old_ids = _authorization_set(prior.get(field, ''))
        expected = {_replace_ids(shot, ids) for shot in old_ids}
        if expected != _authorization_set(current.get(field, '')):
            errors.append(f'renumber 的 {field} 必须按完整镜号映射同步，不能把授权转给别的镜头')
    for field in ('source_sha256', 'scene_images_sha256',
                  'director_plan_sha256', 'scene_facts_sha256', 'input_manifest_sha256'):
        if current.get(field) != prior.get(field):
            errors.append(f'renumber 不得同时改变 {field} 对应的创作来源；先独立完成 local 修订')
    old_dialogue = source_file(base, prior.get('dialogue_manifest', ''), '重编号前对白记录', errors)
    new_dialogue = source_file(path, current.get('dialogue_manifest', ''), '重编号后对白记录', errors)
    if old_dialogue is not None and new_dialogue is not None:
        check_digest(old_dialogue, prior.get('dialogue_manifest_sha256', ''), '重编号前对白记录', errors)
        check_digest(new_dialogue, current.get('dialogue_manifest_sha256', ''), '重编号后对白记录', errors)
        old_record, new_record = json.loads(read_text(old_dialogue)), json.loads(read_text(new_dialogue))
        legacy = isinstance(old_record, list) or (
            isinstance(old_record, dict) and 'stage1_speech' not in old_record)
        if legacy:
            # Historical renumbering kept the dialogue file byte-identical.
            # Current V2 validation still requires explicit speech records;
            # upgrade in a new local revision, never rewrite a historical base.
            if old_dialogue.read_bytes() != new_dialogue.read_bytes():
                errors.append('旧格式对白记录不得在renumber中改写；先另存正文和镜号不变的local制作副本，'
                              '在新记录中补齐发声定位，再以该副本重编号；保留旧稿与旧记录原件')
        elif not isinstance(old_record, dict) or not isinstance(old_record.get('stage1_speech'), dict):
            errors.append('重编号前对白记录的stage1_speech格式错误，不得作为已确认的定位依据')
        else:
            expected_record = dict(old_record)
            old_speech = old_record['stage1_speech']
            if set(old_speech) != set(ids):
                errors.append('重编号前对白记录必须与旧主序列镜号一致')
            expected_record['stage1_speech'] = {ids.get(shot, shot): lines for shot, lines in old_speech.items()}
            if expected_record != new_record:
                errors.append('renumber 对白记录只能同步镜号，不得改变台词、原剧本定位或发声选择')
    def coverage(value):
        tables = [table for table in find_tables(value) if is_coverage_table(table)]
        if any(table.headers != HEADERS or any(len(row) != len(HEADERS) for row in table.rows) for table in tables):
            raise ValueError('renumber 不能跳过坏行或未知格式的覆盖表')
        return [row for table in tables for row in table.rows]
    old_coverage, new_coverage = coverage(old_text), coverage(text)
    expected_coverage = []
    for row in old_coverage:
        parsed = parse_shot_id(row[1])
        if parsed is None or parsed.coverage is None or f'{parsed.scene}{parsed.letters}' not in ids:
            raise ValueError('renumber 旧覆盖镜必须引用真实旧主镜')
        transformed = list(row)
        transformed[1] = _replace_ids(row[1], ids)
        transformed[6] = _replace_ids(row[6], ids)
        expected_coverage.append(tuple(transformed))
    if expected_coverage != new_coverage:
        errors.append('renumber 的覆盖表必须完整保留并同步 COV 基础镜号及剪辑引用；不得错指或改动表演')
    old_notes = source_file(base, prior.get('continuity_notes', ''), '重编号前连续性记录', errors)
    new_notes = source_file(path, current.get('continuity_notes', ''), '重编号后连续性记录', errors)
    if old_notes is not None and new_notes is not None:
        check_digest(old_notes, prior.get('continuity_notes_sha256', ''), '重编号前连续性记录', errors)
        check_digest(new_notes, current.get('continuity_notes_sha256', ''), '重编号后连续性记录', errors)
        if _replace_ids(read_text(old_notes), ids) != read_text(new_notes):
            errors.append('renumber 连续性记录必须保留原意并按实际映射更新镜号；不能沿用已指向其他镜头的旧编号')


def numbering_errors(path: Path, text: str | None = None, *, seen: tuple[Path, ...] = ()) -> tuple[list[str], dict | None]:
    """Validate only historical numbering provenance, not stale historical assets."""
    errors = []
    path = path.resolve()
    if path in seen or len(seen) >= 100:
        return ['修订记录存在循环或超过 100 版追溯上限'], None
    try:
        text = read_text(path) if text is None else text
        parsed = parse_frontmatter(text)
        errors.extend(parsed.errors)
        front = parsed.values
        rows = _rows(text)
        declared = 'revision_record' in front or 'revision_record_sha256' in front
        if not declared:
            from validate_v2_prompts import normalized_shot_sequence_errors
            return errors + normalized_shot_sequence_errors([row[1] for row in rows]), None
        if Path(front.get('revision_record', '')).is_absolute():
            errors.append('revision_record 必须使用项目相对路径')
        record = source_file(path, front.get('revision_record', ''), 'Stage 1 修订记录', errors)
        if record is None:
            return errors, None
        check_digest(record, front.get('revision_record_sha256', ''), 'Stage 1 修订记录', errors)
        payload = json.loads(read_text(record))
        required = {'version', 'mode', 'reason', 'base_stage1', 'base_stage1_sha256', 'changes', 'mapping'}
        if not isinstance(payload, dict) or set(payload) != required or type(payload['version']) is not int or payload['version'] != 1:
            return errors + ['修订记录必须为 version 1 并完整使用规定字段'], None
        if not isinstance(payload['reason'], str) or not payload['reason'].strip():
            errors.append('修订记录须说明本次局部修改或确需重编的理由')
        if not isinstance(payload['base_stage1'], str) or Path(payload['base_stage1']).is_absolute():
            errors.append('base_stage1 必须使用相对修订记录的旧稿路径')
        base = source_file(record, payload['base_stage1'], '修订旧 Stage 1', errors)
        if base is None:
            return errors, None
        check_digest(base, payload['base_stage1_sha256'], '修订旧 Stage 1', errors)
        old_text = read_text(base)
        old_front = parse_frontmatter(old_text).values
        if old_front.get('artifact') != 'horizontal-tv-shotlist' or old_front.get('status') != 'approved':
            errors.append('修订旧稿必须为实际 approved Stage 1；未批准新稿继续默认规范编号')
        for field in ('project', 'episode'):
            if front.get(field) != old_front.get(field):
                errors.append(f'修订旧新 Stage 1 的 {field} 必须一致')
        base_errors, _ = numbering_errors(base, old_text, seen=seen + (path,))
        errors.extend('旧稿编号依据：' + e for e in base_errors)
        mapping = build_mapping(old_text, text, payload['mode'], payload['changes'])
        if payload['mode'] == 'renumber':
            _renumber_related(path, base, text, old_text, mapping, errors)
        if payload['mapping'] != mapping:
            errors.append('修订 mapping 与实际旧新主序列的完整编号、行序或内容摘要不一致')
        if errors:
            return errors, None
        return [], {'path': str(record), 'sha256': digest(record), 'mode': payload['mode'],
                    'base_stage1': str(base), 'base_stage1_sha256': digest(base), 'mapping': mapping}
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        return errors + [f'修订编号依据无法校验：{exc}'], None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--mode', choices=('local', 'renumber'), required=True)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--changes', type=Path, help='local 的实际变更数组；renumber 不传')
    parser.add_argument('--output', type=Path, required=True, help='新修订记录文件，不覆盖既有文件')
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError('output 已存在；为保留历史，请使用新修订记录路径')
        from generation_tasks import _InputSnapshot
        snapshot = _InputSnapshot(args.base, kind='renumber_base' if args.mode == 'renumber' else 'revision_base')
        old = snapshot.stage3
        current = snapshot._capture(args.current, 'revision_base')
        changes = []
        if args.changes:
            change_path = snapshot._capture(args.changes, 'revision_changes')
            changes = json.loads(snapshot.texts[change_path])
        errors, _ = numbering_errors(old)
        if errors:
            raise ValueError('；'.join(errors))
        old_text, current_text = snapshot.texts[old], snapshot.texts[current]
        old_front, new_front = parse_frontmatter(old_text).values, parse_frontmatter(current_text).values
        if old_front.get('status') != 'approved' or old_front.get('artifact') != 'horizontal-tv-shotlist':
            raise ValueError('base 必须为 approved Stage 1')
        if any(old_front.get(k) != new_front.get(k) for k in ('project', 'episode')) or not args.reason.strip():
            raise ValueError('旧新稿须为同项目同集，reason 不得为空')
        payload = {'version': 1, 'mode': args.mode, 'reason': args.reason,
                   'base_stage1': os.path.relpath(old, args.output.parent.resolve()), 'base_stage1_sha256': snapshot.record(old)['sha256'],
                   'changes': changes, 'mapping': build_mapping(old_text, current_text, args.mode, changes)}
        # Current file hash is intentionally absent: Stage 1 binds this record.
        snapshot.verify_unchanged()
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        print('revision_record: ' + os.path.relpath(args.output.resolve(), current.parent))
        print('revision_record_sha256: ' + digest(args.output))
        print('OK: 记录已生成；将以上字段绑定到新 Stage 1，再运行完整 V2 校验。')
        return 0
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        print('ERROR: ' + str(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
