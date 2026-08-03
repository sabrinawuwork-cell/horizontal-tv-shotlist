#!/usr/bin/env python3
"""Dependency-free parsing helpers for horizontal shotlist tooling."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Iterable, Sequence


HEADERS = (
    "场次",
    "镜号",
    "时长",
    "摄影机",
    "画面内容",
    "音效",
    "剪辑衔接",
)
SHOT_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
LETTER_VALUES = {letter: index for index, letter in enumerate(SHOT_LETTERS, 1)}


@dataclass(frozen=True)
class Frontmatter:
    values: dict[str, str]
    body_start_line: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarkdownTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    header_line: int
    row_lines: tuple[int, ...]
    context: str


@dataclass(frozen=True)
class ShotId:
    scene: str
    letters: str
    insertion: int | None = None
    coverage: int | None = None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def parse_frontmatter(text: str) -> Frontmatter:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return Frontmatter({}, 1, ("缺少以 --- 开始的 frontmatter",))
    closing = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if closing is None:
        return Frontmatter({}, 1, ("frontmatter 缺少结束分隔符 ---",))

    values: dict[str, str] = {}
    errors: list[str] = []
    active_mapping: str | None = None
    for line_no, raw in enumerate(lines[1:closing], start=2):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw[:1].isspace():
            if active_mapping and ":" in stripped:
                key, value = stripped.split(":", 1)
                entry = f"{key.strip()}: {value.strip()}"
                values[active_mapping] = (
                    values.get(active_mapping, "") + "\n" + entry
                ).strip()
            else:
                errors.append(f"第 {line_no} 行：不支持的 frontmatter 缩进")
            continue
        if ":" not in raw:
            errors.append(f"第 {line_no} 行：frontmatter 字段必须使用 key: value")
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key):
            errors.append(f"第 {line_no} 行：非法 frontmatter 键 {key!r}")
            continue
        if key in values:
            errors.append(f"第 {line_no} 行：frontmatter 字段 {key!r} 重复")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
        active_mapping = key if not value else None
    return Frontmatter(values, closing + 2, tuple(errors))


def split_markdown_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    in_code = False
    for char in stripped[1:]:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == "`":
            current.append(char)
            in_code = not in_code
        elif char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current or not stripped.endswith("|"):
        cells.append("".join(current).strip())
    return cells


def _separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells
    )


def _visible_lines(lines: Sequence[str]) -> list[str]:
    visible: list[str] = []
    fence: str | None = None
    in_comment = False
    for line in lines:
        stripped = line.lstrip()
        if fence:
            visible.append("")
            if stripped.startswith(fence):
                fence = None
            continue
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            fence = marker.group(1)
            visible.append("")
            continue
        output = line
        if in_comment:
            if "-->" not in output:
                visible.append("")
                continue
            output = output.split("-->", 1)[1]
            in_comment = False
        while "<!--" in output:
            before, after = output.split("<!--", 1)
            if "-->" in after:
                output = before + after.split("-->", 1)[1]
            else:
                output = before
                in_comment = True
        visible.append(output)
    return visible


def find_tables(text: str) -> list[MarkdownTable]:
    lines = text.splitlines()
    visible = _visible_lines(lines)
    tables: list[MarkdownTable] = []
    index = 0
    while index + 1 < len(lines):
        headers = split_markdown_row(visible[index])
        separator = split_markdown_row(visible[index + 1])
        if not headers or not separator or not _separator(separator):
            index += 1
            continue
        row_index = index + 2
        rows: list[tuple[str, ...]] = []
        row_lines: list[int] = []
        while row_index < len(lines):
            cells = split_markdown_row(visible[row_index])
            if cells is None or _separator(cells):
                break
            rows.append(tuple(cells))
            row_lines.append(row_index + 1)
            row_index += 1
        context = ""
        for prior in range(index - 1, max(-1, index - 12), -1):
            candidate = visible[prior].strip()
            if candidate.startswith("#"):
                context = re.sub(r"^#+\s*", "", candidate).strip()
                break
        tables.append(
            MarkdownTable(
                tuple(headers), tuple(rows), index + 1, tuple(row_lines), context
            )
        )
        index = max(row_index, index + 2)
    return tables


def strip_inline(value: str) -> str:
    value = value.strip()
    for marker in ("**", "__", "`", "*"):
        if value.startswith(marker) and value.endswith(marker):
            value = value[len(marker) : -len(marker)].strip()
    return value


def parse_duration(value: str) -> Decimal:
    cleaned = strip_inline(value)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|秒)", cleaned, re.I)
    if not match:
        raise ValueError("时长必须只写秒数，例如 6s 或 18.5s")
    try:
        duration = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise ValueError("时长包含非法数字") from exc
    if not duration.is_finite():
        raise ValueError("时长必须使用有限数字")
    return duration


def parse_shot_id(value: str) -> ShotId | None:
    shot_id = strip_inline(value).upper()
    match = re.fullmatch(
        r"(?P<scene>.+?)(?P<letters>[A-Z]+)(?:(?:-(?P<insert>[1-9]\d*))|(?:-COV(?P<cov>[1-9]\d*)))?",
        shot_id,
    )
    if not match:
        return None
    letters = match.group("letters")
    if any(letter not in LETTER_VALUES for letter in letters):
        return None
    return ShotId(
        match.group("scene"),
        letters,
        int(match.group("insert")) if match.group("insert") else None,
        int(match.group("cov")) if match.group("cov") else None,
    )


def shot_rank(letters: str) -> int:
    rank = 0
    for letter in letters:
        rank = rank * len(SHOT_LETTERS) + LETTER_VALUES[letter]
    return rank


def require_fields(values: dict[str, str], required: Iterable[str]) -> list[str]:
    return [
        f"frontmatter 缺少非空字段 {key!r}"
        for key in required
        if not values.get(key, "").strip()
    ]


def format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")


def is_coverage_table(table: MarkdownTable) -> bool:
    if "必要覆盖" in table.context:
        return True
    shot_index = table.headers.index("镜号") if "镜号" in table.headers else -1
    return shot_index >= 0 and bool(table.rows) and all(
        "-COV" in row[shot_index].upper()
        for row in table.rows
        if len(row) > shot_index
    )
