#!/usr/bin/env python3
"""Mechanically replace Stage 3 draft category names with approved Stage 2 text."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from shotlist_common import read_text
from validate_stage2_fixed_prompts import category_map


TOKEN_RE = re.compile(r"〔([^〔〕]+)〕")


def expand_text(draft_text: str, stage2_texts: list[str]) -> tuple[str, list[str]]:
    errors: list[str] = []
    categories: dict[str, str] = {}
    for text in stage2_texts:
        mapping, stage_errors, values = category_map(text)
        errors.extend(stage_errors)
        if values.get("status") != "approved":
            errors.append(f"场次 {values.get('scene', '?')} 的 Stage 2 尚未 approved")
        for name, prompt in mapping.items():
            if name in categories and categories[name] != prompt:
                errors.append(f"多个 Stage 2 文件对类别 {name} 给出不同正文")
            categories[name] = prompt
    used = TOKEN_RE.findall(draft_text)
    for name in used:
        if name not in categories:
            errors.append(f"Stage 3 draft 使用了不存在的固定类别：{name}")
    if errors:
        return draft_text, errors

    expanded = TOKEN_RE.sub(lambda match: categories[match.group(1)], draft_text)
    expanded, count = re.subn(r"(?m)^status:\s*draft\s*$", "status: approved", expanded, count=1)
    if count != 1:
        errors.append("Stage 3 draft frontmatter 必须且只能包含 status: draft")
    return expanded, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--stage2", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expanded, errors = expand_text(read_text(args.draft), [read_text(path) for path in args.stage2])
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    args.output.write_text(expanded, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
