"""Read explicit content structure; never classify meaning from vocabulary.

Speech selections are authored in the existing internal records. They identify
text, not a proof that an actor speaks it. Source-grounded content review owns
that classification and completeness; these helpers check the declared data.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import re


CATEGORY_NAMES = ('画面', '动作/表情', '情感解析', '声音', '光影', '背景', '环境', '群演', '外观')
VISUAL_NAMES = ('光影', '背景', '环境', '群演', '外观')
KNOWN_NAMES = CATEGORY_NAMES + ('音色', '音色状态', '固定类别', '剪辑')
_FOCAL = re.compile(
    r'(?<![\d.])(-?\d+(?:\.\d+)?)'
    r'(?:\s*(?:[–—~～-]|至|到)\s*(\d+(?:\.\d+)?))?\s*(?:mm|毫米)(?![A-Za-z])', re.I)
_QUOTE_PAIRS = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"'}
_CARD_REFERENCE = re.compile(r'〔[^〔〕\n]+·[^〔〕\n]+〕')


def normalized_text(text: str) -> str:
    return re.sub(r'<br\s*/?>', '\n', text, flags=re.I).replace('\r\n', '\n').replace('\r', '\n')


@dataclass(frozen=True)
class PromptBlock:
    text: str
    label: str | None
    body: str


def prompt_blocks(text: str) -> list[PromptBlock]:
    """Blank-line blocks, respecting quoted text; labels only at block starts."""
    raw = normalized_text(text).strip()
    chunks: list[str] = []
    start = 0
    index = 0
    closing: list[str] = []
    while index < len(raw):
        char = raw[index]
        if char == '\\' and index + 1 < len(raw):
            index += 2
            continue
        if closing and char == closing[-1]:
            closing.pop()
        elif char in _QUOTE_PAIRS:
            closing.append(_QUOTE_PAIRS[char])
        separator = re.match(r'\n(?:[ \t]*\n)+', raw[index:]) if char == '\n' and not closing else None
        if separator:
            chunks.append(raw[start:index].strip())
            index += separator.end()
            start = index
            continue
        index += 1
    chunks.append(raw[start:].strip())
    result = []
    for chunk in chunks:
        if not chunk:
            continue
        label = next((name for name in KNOWN_NAMES if chunk.startswith(name + '：')), None)
        body = chunk[len(label) + 1:].strip() if label else chunk
        result.append(PromptBlock(chunk, label, body))
    return result


def focal_lengths(camera: str) -> list[Decimal]:
    """Explicit millimetre values, including decimal values and zoom endpoints."""
    result = []
    for match in _FOCAL.finditer(camera):
        result.append(Decimal(match[1]))
        if match[2] is not None:
            result.append(Decimal(match[2]))
    return result


def card_reference_errors(text: str) -> list[str]:
    """Reject reserved, unexpanded card tokens outside literal quotations."""
    raw = normalized_text(text)
    errors: list[str] = []
    closing: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if closing and char == '\\' and index + 1 < len(raw):
            index += 2
            continue
        if closing and char == closing[-1]:
            closing.pop()
        elif char in _QUOTE_PAIRS:
            closing.append(_QUOTE_PAIRS[char])
        elif not closing:
            match = _CARD_REFERENCE.match(raw, index)
            if match:
                errors.append(f'固定卡引用未展开：{match[0]}；须写入已选卡片的实际正文')
                index = match.end()
                continue
        index += 1
    if closing and _CARD_REFERENCE.search(raw):
        errors.append('引号未闭合，无法确认固定卡标记是字面引用还是未展开的结构引用')
    return errors


def quoted_texts(text: str) -> list[str]:
    """Read outer quoted strings in an explicitly selected source range.

    This is delimiter parsing, not a claim that arbitrary quotes are speech.
    """
    result = []
    closing: list[str] = []
    start = 0
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if closing and char == closing[-1]:
            closing.pop()
            if not closing:
                result.append(text[start:index])
        elif char in _QUOTE_PAIRS:
            if not closing:
                start = index + 1
            closing.append(_QUOTE_PAIRS[char])
    if closing:
        raise ValueError('已选对白范围内引号未闭合；可使用source_speech明确原文位置')
    return result


def select_speech(text: str, entries: object, label: str) -> tuple[list[str], list[tuple[int, int]], list[str]]:
    """Select declared actual utterances by exact text and optional occurrence.

    A string shorthand is accepted only when unique. Occurrence is one-based;
    ambiguous repeated text must be explicitly disambiguated by the author.
    """
    raw = normalized_text(text)
    if not isinstance(entries, list):
        return [], [], [f'{label}缺少明确发声记录；由代理补充现有内部记录，不能按引号猜测对白']
    values: list[str] = []
    spans: list[tuple[int, int]] = []
    errors: list[str] = []
    previous_end = 0
    for index, entry in enumerate(entries, 1):
        if isinstance(entry, str):
            value, occurrence = entry, None
        elif isinstance(entry, dict) and set(entry) <= {'text', 'occurrence'}:
            value, occurrence = entry.get('text'), entry.get('occurrence')
        else:
            errors.append(f'{label}第{index}项必须为原文字符串或text/occurrence对象')
            continue
        if not isinstance(value, str) or not value.strip():
            errors.append(f'{label}第{index}项发声文本不能为空')
            continue
        if occurrence is not None and (type(occurrence) is not int or occurrence < 1):
            errors.append(f'{label}第{index}项occurrence必须为正整数')
            continue
        value = normalized_text(value)
        matches = [m.start() for m in re.finditer(re.escape(value), raw)]
        if not matches:
            errors.append(f'{label}第{index}项文本不在对应正文中：{value}')
            continue
        if occurrence is None:
            if len(matches) != 1:
                errors.append(f'{label}第{index}项文本出现多次，须明确实际发声对应的occurrence')
                continue
            occurrence = 1
        if occurrence > len(matches):
            errors.append(f'{label}第{index}项指定的出现位置不存在')
            continue
        start = matches[occurrence - 1]
        end = start + len(value)
        if spans and start < previous_end:
            errors.append(f'{label}第{index}项发声位置重叠或不按正文顺序')
            continue
        values.append(value)
        spans.append((start, end))
        previous_end = end
    return values, spans, errors


def speech_by_shot(rows: list[tuple[str, ...]], mapping: object) -> tuple[dict[str, list[str]], list[str]]:
    expected = {row[1].upper() for row in rows}
    if not isinstance(mapping, dict):
        return {}, ['对白清单缺少stage1_speech；由代理标明各镜实际发声，无对白镜头使用空数组']
    errors = []
    if set(mapping) != expected:
        errors.append('stage1_speech必须覆盖全部且仅当前主序列镜号')
    result = {}
    for row in rows:
        shot = row[1].upper()
        values, _spans, selected_errors = select_speech(row[4], mapping.get(shot), f'Stage1镜头{shot}')
        errors.extend(selected_errors)
        result[shot] = values
    return result, errors


def mask_spans(text: str, spans: list[tuple[int, int]]) -> str:
    raw = normalized_text(text)
    for start, end in reversed(spans):
        raw = raw[:start] + (' ' * (end - start)) + raw[end:]
    return raw


def visual_blocks(texts: list[str]) -> list[str]:
    """Assemble approved visual card bodies without paraphrasing or deduping.

    Repeated labels are container syntax. Each body remains in its selected
    order, separated by a line break under a single category heading.
    """
    grouped: dict[str, list[str]] = defaultdict(list)
    for text in texts:
        raw = normalized_text(text).strip()
        label = next((name for name in VISUAL_NAMES if raw.startswith(name + '：')), None)
        if label:
            grouped[label].append(raw[len(label) + 1:].strip())
    return [name + '：' + '\n'.join(grouped[name]) for name in VISUAL_NAMES if grouped[name]]


def canonical_prompt_blocks(text: str) -> list[str]:
    """Merge visual labels only; preserve all camera, action and sound blocks."""
    parsed = prompt_blocks(text)
    visuals = visual_blocks([block.text for block in parsed if block.label in VISUAL_NAMES])
    by_label = {block.split('：', 1)[0]: block for block in visuals}
    emitted: set[str] = set()
    result = []
    for block in parsed:
        if block.label in VISUAL_NAMES:
            if block.label not in emitted:
                result.append(by_label[block.label])
                emitted.add(block.label)
        else:
            result.append(block.text)
    return result
