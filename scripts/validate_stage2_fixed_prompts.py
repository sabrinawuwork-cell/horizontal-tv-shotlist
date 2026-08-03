#!/usr/bin/env python3
"""Validate a scene-approved Stage 2 category-card authority."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys

from shotlist_common import (
    HEADERS as STAGE1_HEADERS,
    find_tables,
    is_coverage_table,
    parse_frontmatter,
    read_text,
    require_fields,
)


PREFIXES = (
    "基础音色·", "场次音色·", "阶段音色·", "光影·", "背景·", "环境·",
    "群演·", "场次声音·",
)
REQUIRED = (
    "artifact", "project", "episode", "version", "status", "source_stage1",
    "scene", "scene_image", "stage1_sha256", "speaking_characters",
    "environment_required", "extras_required", "scene_sound_required",
)
SHOT_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9])\d{1,3}[A-HJ-NP-Z]+(?:-\d*[A-HJ-NP-Z]+|-\d+)?(?:\s*[-–—至]\s*\d{1,3}[A-HJ-NP-Z]+)?(?![A-Za-z0-9])",
    re.I,
)


def _cards(text: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    errors: list[str] = []
    lines = text.splitlines()
    cards: list[tuple[str, str, int]] = []
    heading_indexes = [
        index for index, line in enumerate(lines)
        if re.match(r"^###\s+\S", line.strip())
    ]
    for position, index in enumerate(heading_indexes):
        name = re.sub(r"^###\s+", "", lines[index].strip()).strip()
        end = heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
        body_lines = [line.strip() for line in lines[index + 1:end] if line.strip()]
        prompt = "<br>".join(body_lines)
        cards.append((name, prompt, index + 1))
    return cards, errors


def _contains_section(prompt: str, label: str) -> bool:
    return bool(re.search(rf"(?:^|<br>){re.escape(label)}\s*[:：]", prompt))


def _numeric_ranges(prompt: str, unit: str) -> int:
    if unit == "K":
        pattern = r"\d{3,5}(?:\s*[–—-]\s*\d{3,5})?\s*K\b"
    else:
        pattern = r"\d+(?:\.\d+)?(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*lux"
    return len(re.findall(pattern, prompt, re.I))


def category_map(text: str) -> tuple[dict[str, str], list[str], dict[str, str]]:
    errors: list[str] = []
    frontmatter = parse_frontmatter(text)
    errors.extend(frontmatter.errors)
    errors.extend(require_fields(frontmatter.values, REQUIRED))
    values = frontmatter.values
    if values.get("artifact") and values["artifact"] != "horizontal-tv-stage2":
        errors.append("artifact 必须为 horizontal-tv-stage2")
    if values.get("status") not in {"draft", "approved"}:
        errors.append("Stage 2 status 只能是 draft 或 approved")
    if values.get("scene_image") and Path(values["scene_image"]).is_absolute():
        errors.append("scene_image 不得写本机绝对路径")
    if any(table.headers == ("固定类别", "固定提示词") for table in find_tables(text)):
        errors.append("Stage 2 已改用类别卡片，不再使用固定提示词两列表格")

    cards, card_errors = _cards(text)
    errors.extend(card_errors)
    categories: dict[str, str] = {}
    for name, prompt, line_no in cards:
        if not name.startswith(PREFIXES):
            errors.append(f"第 {line_no} 行：未知固定类别前缀 {name}")
        if len(name) > 32 or not re.search(r"[\u3400-\u9fff]", name):
            errors.append(f"第 {line_no} 行：固定类别名称必须是32字以内的简短中文语义名称")
        if SHOT_RANGE_RE.search(name) or SHOT_RANGE_RE.search(prompt) or "适用镜号" in prompt:
            errors.append(f"第 {line_no} 行：Stage 2 卡片不得包含镜号或镜号范围")
        if name in categories:
            errors.append(f"第 {line_no} 行：固定类别名称重复 {name}")
        if not prompt:
            errors.append(f"第 {line_no} 行：固定类别卡片正文不得为空")
        if re.search(r"同上|保持不变|\{\{|\}\}|<待替换>", prompt):
            errors.append(f"第 {line_no} 行：固定提示词不得依赖同上或占位符")
        if "无配乐无字幕" in prompt:
            errors.append(f"第 {line_no} 行：无配乐无字幕只允许由 Stage 3 在镜头末尾添加")
        if re.search(r"视频里?不出现红蓝色标记", prompt):
            errors.append(f"第 {line_no} 行：红蓝标记等临时限制不得进入 Stage 2")
        if "保留人物真实皮肤质感与瑕疵" in prompt:
            errors.append(f"第 {line_no} 行：通用皮肤质感句由 Stage 3 按可见性添加")
        if re.search(r"电影质感|高级感|高级", prompt) and not re.search(
            r"光源|主光|侧光|逆光|照亮|阴影|照度|色温|lux|\bK\b|曝光|反差", prompt, re.I
        ):
            errors.append(f"第 {line_no} 行：氛围词之前缺少可执行光影依据")

        if name.startswith("基础音色·") and not _contains_section(prompt, "音色"):
            errors.append(f"第 {line_no} 行：基础音色卡片必须使用音色：段落")
        if name.startswith(("场次音色·", "阶段音色·")) and not _contains_section(prompt, "音色状态"):
            errors.append(f"第 {line_no} 行：音色状态卡片必须使用音色状态：段落")
        if name.startswith("光影·") and not _contains_section(prompt, "光影"):
            errors.append(f"第 {line_no} 行：光影卡片必须使用光影：段落")
        if name.startswith("背景·") and not _contains_section(prompt, "背景"):
            errors.append(f"第 {line_no} 行：背景卡片必须使用背景：段落")
        if name.startswith("环境·") and not _contains_section(prompt, "环境"):
            errors.append(f"第 {line_no} 行：环境卡片必须使用环境：段落")
        if name.startswith("群演·") and not _contains_section(prompt, "群演"):
            errors.append(f"第 {line_no} 行：群演卡片必须使用群演：段落")
        if name.startswith("场次声音·") and not _contains_section(prompt, "声音"):
            errors.append(f"第 {line_no} 行：场次声音卡片必须使用声音：段落")
        if name.startswith("光影·"):
            if _numeric_ranges(prompt, "K") < 1:
                errors.append(f"第 {line_no} 行：光影卡片必须给出主光色温 K 数值或区间")
            if _numeric_ranges(prompt, "lux") < 3:
                errors.append(f"第 {line_no} 行：光影卡片必须分别给出人物亮面、暗面和背景 lux")
            if "背景" not in prompt:
                errors.append(f"第 {line_no} 行：光影卡片必须压缩写入主要背景及背景照度")
        if name.startswith("背景·") and _numeric_ranges(prompt, "lux") < 1:
            errors.append(f"第 {line_no} 行：独立背景卡必须给出背景 lux")
        categories[name] = prompt

    speakers = [
        item.strip() for item in re.split(r"[,，]", values.get("speaking_characters", ""))
        if item.strip() and item.strip().lower() != "none"
    ]
    for speaker in speakers:
        if f"基础音色·{speaker}" not in categories:
            errors.append(f"说话角色 {speaker} 缺少基础音色类别")

    conditional = (
        ("environment_required", "环境·", "环境"),
        ("extras_required", "群演·", "群演"),
        ("scene_sound_required", "场次声音·", "场次声音"),
    )
    for field, prefix, label in conditional:
        decision = values.get(field, "").lower()
        if decision not in {"yes", "no"}:
            errors.append(f"{field} 必须明确写 yes 或 no")
            continue
        present = any(name.startswith(prefix) for name in categories)
        if decision == "yes" and not present:
            errors.append(f"{field}=yes 但缺少{label}类别")
        if decision == "no" and present:
            errors.append(f"{field}=no 但出现了{label}类别")

    if values.get("status") == "approved" and any(
        re.search(r"待确认|待补|TBD", value, re.I) for value in categories.values()
    ):
        errors.append("approved Stage 2 不得含待确认项")
    return categories, errors, values


def validate_against_stage1(
    stage2_text: str, stage1_text: str, *, source_name: str | None = None
) -> list[str]:
    _categories, errors, values = category_map(stage2_text)
    source_values = parse_frontmatter(stage1_text).values
    scene = values.get("scene", "")
    if source_values.get("status") != "approved":
        errors.append("Stage 2 只能绑定 approved Stage 1")
    if values.get("project") != source_values.get("project") or values.get("episode") != source_values.get("episode"):
        errors.append("Stage 2 project/episode 与 Stage 1 不一致")
    if values.get("stage1_sha256") != hashlib.sha256(stage1_text.encode("utf-8")).hexdigest():
        errors.append("Stage 2 stage1_sha256 与当前 Stage 1 不一致")
    if source_name and values.get("source_stage1") != source_name:
        errors.append("Stage 2 source_stage1 与当前文件名不一致")
    if values.get("scene_image") and f"场景图：{values['scene_image']}" not in stage1_text:
        errors.append(f"场次 {scene} 的 scene_image 与 Stage 1 不一致")
    source_rows = [
        row for table in find_tables(stage1_text)
        if table.headers == STAGE1_HEADERS and not is_coverage_table(table)
        for row in table.rows if row[0].strip() == scene
    ]
    if not source_rows:
        errors.append(f"Stage 1 不存在场次 {scene}")
    speakers = [
        item.strip() for item in re.split(r"[,，]", values.get("speaking_characters", ""))
        if item.strip() and item.strip().lower() != "none"
    ]
    for speaker in speakers:
        # Stage 1 may establish the named speaker once, then bind the line with a
        # pronoun in the same continuous picture paragraph.  Require same-row
        # co-occurrence instead of guessing Chinese coreference from word distance.
        speaks_in_row = any(
            speaker in row[4] and re.search(r"[“\"][^”\"]+[”\"]", row[4])
            for row in source_rows
        )
        if not speaks_in_row:
            errors.append(f"speaking_characters 中的 {speaker} 未在该场 Stage 1 对白中发声")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--stage1", type=Path, required=True)
    args = parser.parse_args()
    stage2_text = read_text(args.path)
    categories, _errors, _values = category_map(stage2_text)
    errors = validate_against_stage1(
        stage2_text, read_text(args.stage1), source_name=args.stage1.name
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path} ({len(categories)} 个固定类别卡片)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
