#!/usr/bin/env python3
"""Validate the V2 Stage 2 minimal-card protocol without changing V1 validators."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from shotlist_common import parse_frontmatter, read_text, require_fields
from validate_stage2_fixed_prompts import _cards
from validate_v2_prompts import light_errors


REQUIRED = (
    "artifact",
    "project",
    "episode",
    "version",
    "status",
    "source_stage1",
    "scene",
    "scene_image",
    "stage1_sha256",
    "speaking_characters",
    "voice_prompt_required",
    "environment_required",
    "extras_required",
    "scene_sound_required",
)
PREFIXES = (
    "基础音色·",
    "场次音色·",
    "阶段音色·",
    "光影·",
    "背景·",
    "环境·",
    "群演外观·",
    "群演动作·",
    "群演光影·",
    "场次声音·",
)


def validate_text(text: str) -> list[str]:
    errors: list[str] = []
    front = parse_frontmatter(text)
    errors.extend(front.errors)
    errors.extend(require_fields(front.values, REQUIRED))
    values = front.values
    if values.get("artifact") != "horizontal-tv-stage2":
        errors.append("artifact必须为horizontal-tv-stage2")
    if values.get("status") not in {"draft", "approved"}:
        errors.append("Stage 2 status只能是draft或approved")

    cards, card_errors = _cards(text)
    errors.extend(card_errors)
    categories: dict[str, str] = {}
    for name, prompt, line_no in cards:
        if not name.startswith(PREFIXES):
            errors.append(f"第{line_no}行：未知V2固定类别前缀 {name}")
        if name in categories:
            errors.append(f"第{line_no}行：固定类别重复 {name}")
        if any(token in prompt for token in ("无配乐无字幕", "【角色名音色参考：】", "保持far-side key", "保持 far-side key")):
            errors.append(f"第{line_no}行：固定卡含Stage 3限制或抽象光影元语言")
        if name.startswith("光影·"):
            errors.extend(f"第{line_no}行：{item}" for item in light_errors(prompt))
        categories[name] = prompt

    speakers = [
        item.strip()
        for item in re.split(r"[,，]", values.get("speaking_characters", ""))
        if item.strip() and item.strip().lower() != "none"
    ]
    voice_required = values.get("voice_prompt_required", "").lower()
    if voice_required not in {"yes", "no"}:
        errors.append("voice_prompt_required必须明确写yes或no")
    voice_cards = {name for name in categories if name.startswith("基础音色·")}
    if voice_required == "no" and voice_cards:
        errors.append("当前任务未明确启用音色，不得创建基础音色卡")
    if voice_required == "yes":
        for speaker in speakers:
            if f"基础音色·{speaker}" not in voice_cards:
                errors.append(f"已启用音色，但说话角色{speaker}缺少基础音色卡")

    conditional = (
        ("environment_required", "环境·", "环境"),
        ("extras_required", "群演", "群演"),
        ("scene_sound_required", "场次声音·", "场次声音"),
    )
    for field, prefix, label in conditional:
        decision = values.get(field, "").lower()
        if decision not in {"yes", "no"}:
            errors.append(f"{field}必须明确写yes或no")
            continue
        present = any(name.startswith(prefix) for name in categories)
        if decision == "yes" and not present:
            errors.append(f"{field}=yes但缺少{label}卡")
        if decision == "no" and present:
            errors.append(f"{field}=no但出现{label}卡")

    if values.get("status") == "approved" and re.search(r"待确认|待补|TBD", text, re.I):
        errors.append("approved Stage 2不得含待确认项")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    errors = validate_text(read_text(args.path))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path} 符合V2 Stage 2极简固定类别协议")
    return 0


if __name__ == "__main__":
    sys.exit(main())
