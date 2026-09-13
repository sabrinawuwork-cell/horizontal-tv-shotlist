#!/usr/bin/env python3
"""Validate the V2 Stage 2 minimal-card protocol without changing V1 validators."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from shotlist_common import parse_frontmatter, read_text, require_fields
from validate_stage2_fixed_prompts import _cards
from validate_v2_prompts import light_errors, remove_quotes
from v2_delivery_checks import stage2_sources
from v2_project_inputs import ProjectPolicy, inherited_policy, project_term_errors


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


def validate_text(text: str, *, policy: ProjectPolicy | None = None) -> list[str]:
    # File entry points supply the policy inherited from the actual Stage 1.
    policy = policy or ProjectPolicy()
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
        expected_labels = {
            '基础音色': ('音色：',), '场次音色': ('音色状态：', '音色：'), '阶段音色': ('音色状态：', '音色：'),
            '光影': ('光影：',), '背景': ('背景：',), '环境': ('环境：',),
            '群演外观': ('群演：', '外观：'), '群演动作': ('群演：',),
            '群演光影': ('群演：', '光影：'), '场次声音': ('声音：',),
        }.get(name.split('·', 1)[0], ())
        if not prompt.strip() or not any(prompt.startswith(label) and prompt[len(label):].strip() for label in expected_labels):
            errors.append(f'第{line_no}行：固定卡正文必须非空且使用与卡名对应的类别标签')
        # Whether wording is an instruction, a quoted sign or an inadequate
        # lighting description belongs to content review, not token matching.
        if name.startswith("光影·"):
            errors.extend(f"第{line_no}行：{item}" for item in light_errors(prompt, lighting_mode=policy.lighting_mode))
        errors.extend(f"第{line_no}行：{item}" for item in project_term_errors(prompt, '', policy, set()))
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

    if values.get("status") == "approved":
        unquoted = remove_quotes(re.sub(r'<br\s*/?>', '\n', text, flags=re.I))
        if '🔴' in values.get('blocking_issues', '') or re.search(r'(?:^|[|\n])\s*(?:[-*]\s*)?🔴', unquoted):
            errors.append("approved Stage 2不得含明确的🔴阻塞问题")
        for name, prompt in categories.items():
            if re.fullmatch(r'[^：\n]+：\s*(?:待确认|待补|TBD)[\s。.…]*', prompt, re.I):
                errors.append(f"approved Stage 2固定卡{name}正文仍为待补占位符")
        # Whether prose reports an unresolved problem, denies one, or quotes
        # a document name belongs to content review, not a whole-file word scan.
    return errors


def validate_file(path: Path) -> list[str]:
    try:
        errors, policy = inherited_policy(path)
        errors.extend(validate_text(read_text(path), policy=policy))
        source_errors, source = stage2_sources(path)
        errors.extend(source_errors)
        if source is not None:
            from validate_v2_shotlist import validate_file as validate_stage1
            errors.extend(validate_stage1(source))
        return list(dict.fromkeys(errors))
    except (OSError, UnicodeError, ValueError) as exc:
        return [f'Stage 2 权威来源无法校验：{exc}']


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    errors = validate_file(args.path.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path} 符合V2 Stage 2极简固定类别协议")
    return 0


if __name__ == "__main__":
    sys.exit(main())
