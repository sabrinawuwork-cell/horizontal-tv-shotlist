"""Read scene-global cells and their adjacent shot tables in editing order."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from content_contract import prompt_blocks
from shotlist_common import find_tables, parse_frontmatter

TAIL = '全局禁止出现任何字幕、音乐、非同期声音效、转场。保留真实的微细毛孔、肌肤纹理与皮肤质感。人物表演克制准确。'
LABELS = ('视觉风格', '全局场景参考', '全局人物参考', '全局光影参考', '全局环境参考', '全局群演外观', '全局群演动作')
HEADERS = ('镜号', '片段', '时长', '分镜内容', 'Seedance提示词')


def enabled(text):
    return (parse_frontmatter(text).values.get('stage3_layout') == 'scene-canvas'
            or any('全局提示词' in h for t in find_tables(text) for h in t.headers))


def scoped_label(label):
    """Return (scope, kind) for a location or extras reference heading."""
    for kind in ('环境参考', '外观参考', '动作参考'):
        if label.endswith(kind) and len(label) > len(kind):
            return label[:-len(kind)], kind
    return None


def check_global_cell(cell, scene, *, categories_only=False):
    lines = [line for line in cell.splitlines() if line.strip()]
    headings = []
    values = {}
    supplements = False
    if lines[-1:] != [TAIL]:
        raise ValueError(f'场次 {scene} 的全局固定尾句错误')
    for line in lines[:-1]:
        label, colon, value = line.partition('：')
        label = label.strip()
        if label == '全局场景':
            label = '全局场景参考'
        placeholder = categories_only and (label == '全局光影参考' or scoped_label(label))
        if not colon or (not value.strip() and label != '视觉风格' and not placeholder):
            raise ValueError(f'场次 {scene} 的全局类别缺少标题或内容')
        if label in ('背景', '声音', '音色', '音色状态'):
            supplements = True
            continue
        if supplements or label in values:
            raise ValueError(f'场次 {scene} 的全局类别重复或顺序错误')
        headings.append(label)
        values[label] = value
    if tuple(headings[:4]) != LABELS[:4]:
        raise ValueError(f'场次 {scene} 缺少开头四项或顺序错误')
    optional = headings[4:]
    # Older deliverables used image names and fixed, scene-wide headings.
    named = any(h not in LABELS for h in optional) or any(
        values[h].rstrip().endswith('参考') for h in LABELS[1:3])
    if not named:
        if (optional != [h for h in LABELS[4:] if h in optional]
                or (LABELS[-1] in optional) != (LABELS[-2] in optional)):
            raise ValueError(f'场次 {scene} 的环境或群演类别错误')
        return
    for label in LABELS[1:3]:
        for item in values[label].split('；'):
            if not re.fullmatch(r'[^；：\[\]【】]+参考 {2}', item):
                raise ValueError(f'场次 {scene} 的{label}须逐项写名称、参考和两个半角空格')
    pending = None
    groups_started = False
    for label in optional:
        parsed = scoped_label(label)
        if not parsed or label in LABELS:
            raise ValueError(f'场次 {scene} 须在环境或群演标题中写出具体对象')
        scope, kind = parsed
        if kind == '环境参考' and not groups_started:
            continue
        groups_started = True
        if kind == '外观参考' and pending is None:
            pending = scope
        elif kind == '动作参考' and scope == pending:
            pending = None
        else:
            raise ValueError(f'场次 {scene} 须先列各场地环境，再按同类群演成对列外观、动作')
    if pending:
        raise ValueError(f'场次 {scene} 缺少{pending}动作参考')


def scenes(text):
    categories_only = parse_frontmatter(text).values.get('stage2_content') == 'categories-only'
    tables = [t for t in find_tables(text) if '镜号' in t.headers or any('全局提示词' in h for h in t.headers)]
    if not tables or len(tables) % 2:
        raise ValueError('每场必须先有全局提示词单元格，再有逐镜表')
    result = {}
    for global_table, shots in zip(tables[::2], tables[1::2]):
        match = re.fullmatch(r'场次\s*(\d+(?:-\d+)?)(（续(?:[2-9]\d*|1\d+)?）)?\s*全局提示词', global_table.headers[0])
        if not match or len(global_table.headers) != 1 or len(global_table.rows) != 1 or len(global_table.rows[0]) != 1:
            raise ValueError('全局表须有场号、一列和一个正文单元格')
        scene = match[1]
        section = scene + (match[2] or '')
        if match[2] and scene not in result:
            raise ValueError(f'续场 {section} 缺少首段')
        if section in result or not shots.rows or shots.headers not in (HEADERS, HEADERS + ('备注',)):
            raise ValueError(f'场次 {scene} 的逐镜表或场次顺序错误')
        cell = re.sub(r'<br\s*/?>', '\n', global_table.rows[0][0], flags=re.I)
        check_global_cell(cell, scene, categories_only=categories_only)
        for row in shots.rows:
            if len(row) != len(shots.headers) or not all(x.strip() for x in row[:5]) or not re.match(rf'^{scene}[A-Za-z]', row[0]):
                raise ValueError(f'场次 {scene} 出现错场或不完整的镜头行')
            blocks = prompt_blocks(row[4])
            labels = [b.label for b in blocks]
            if ('动作/表情' not in labels or labels.count('情感解析') != 1
                    or labels.index('情感解析') != labels.index('动作/表情') + 1
                    or not blocks[labels.index('情感解析')].body.strip()):
                raise ValueError(f'{row[0]} 动作/表情后须紧接情感解析')
        result[section] = (cell, shots)
    headers = next(iter(result.values()))[1].headers
    if any(t.headers != headers for _, t in result.values()):
        raise ValueError('各场逐镜表须统一五列或条件六列')
    if len(headers) == 6 and not any(r[5].strip() for _, t in result.values() for r in t.rows):
        raise ValueError('全表无备注时请使用五列')
    return result


if __name__ == '__main__':
    text = Path(sys.argv[1]).read_text(encoding='utf-8-sig')
    groups = scenes(text)
    categories_only = parse_frontmatter(text).values.get('stage2_content') == 'categories-only'
    print(json.dumps([dict(scene=k, globalPrompt=c, headers=t.headers, rows=t.rows, categoriesOnly=categories_only) for k, (c,t) in groups.items()], ensure_ascii=False))
