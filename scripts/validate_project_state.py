#!/usr/bin/env python3
"""Validate that project state advances only from approved shotlists."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

from shotlist_common import find_tables, parse_frontmatter, read_text, require_fields


STATE_HEADERS = ("集号", "已批准分镜", "SHA256")


def validate_state(path: Path) -> list[str]:
    text = read_text(path)
    fm = parse_frontmatter(text)
    errors = list(fm.errors)
    errors.extend(require_fields(fm.values, ("artifact", "project", "version", "approved_through")))
    if fm.values.get("artifact") and fm.values["artifact"] != "horizontal-tv-project-state":
        errors.append("项目状态 artifact 必须为 horizontal-tv-project-state")

    tables = [table for table in find_tables(text) if table.headers == STATE_HEADERS]
    if len(tables) != 1:
        errors.append("项目状态必须且只能包含一张‘集号｜已批准分镜｜SHA256’来源表")
        return errors
    episodes: list[str] = []
    for line_no, row in zip(tables[0].row_lines, tables[0].rows):
        episode, raw_source, digest = (cell.strip() for cell in row)
        source = (path.parent / raw_source).resolve()
        if not source.is_file():
            errors.append(f"第 {line_no} 行：已批准分镜不存在：{raw_source}")
            continue
        source_bytes = source.read_bytes()
        source_fm = parse_frontmatter(source_bytes.decode("utf-8-sig"))
        if source_fm.values.get("status") != "approved":
            errors.append(f"第 {line_no} 行：{raw_source} 不是 approved 分镜")
        if source_fm.values.get("episode") != episode:
            errors.append(f"第 {line_no} 行：集号与分镜 frontmatter 不一致")
        actual = hashlib.sha256(source_bytes).hexdigest()
        if digest.lower() != actual:
            errors.append(f"第 {line_no} 行：{raw_source} 的 SHA256 不匹配")
        episodes.append(episode)
    if episodes and fm.values.get("approved_through") != episodes[-1]:
        errors.append("approved_through 必须等于来源表最后一集")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    errors = validate_state(args.path.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
