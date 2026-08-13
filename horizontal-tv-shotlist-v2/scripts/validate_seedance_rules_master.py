#!/usr/bin/env python3
"""Validate the inline canonical Seedance core and print the A-J integrity report."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys


EXPECTED_SHA256 = "d5b5ddb60ac1c291c46535d3d769815861bae236bde5c049a1a3b6865221abaa"
EXPECTED_CHARACTERS = 8141
EXPECTED_H2 = 24
EXPECTED_NUMBERED_BEFORE_CHECKLIST = 59
EXPECTED_CHECKLIST = 25
EXPECTED_UNNUMBERED_TAIL = 28
EXPECTED_PARAGRAPHS = 260
START = b"<!-- CORE_RULES_START -->\n"
END = b"\n<!-- CORE_RULES_END -->"


def extract_core(data: bytes) -> tuple[bytes, list[str]]:
    errors: list[str] = []
    if data.count(START) != 1 or data.count(END) != 1:
        return b"", ["完整规则文件必须且只能包含一组核心正文边界标记"]
    start = data.index(START) + len(START)
    end = data.index(END, start)
    if end <= start:
        errors.append("核心正文边界顺序错误或正文为空")
    return data[start:end], errors


def _paragraphs(text: str) -> list[str]:
    return [block for block in re.split(r"\n{2,}", text) if block]


def stats(core: bytes) -> dict[str, int | str]:
    text = core.decode("utf-8")
    before, marker, after = text.partition("## 二十四、最终检查清单")
    checklist_block, separator, tail = after.partition("\n\n\n镜头里只保留指定主体")
    return {
        "sha256": hashlib.sha256(core).hexdigest(),
        "characters": len(text),
        "h2": len(re.findall(r"(?m)^## ", text)),
        "numbered": len(re.findall(r"(?m)^\d+\. ", before)),
        "checklist": len(re.findall(r"(?m)^\d+\. ", checklist_block)) if marker else 0,
        "tail": len([line for line in tail.splitlines() if line and not line.startswith("> **")]) if separator else 0,
        "paragraphs": len(_paragraphs(text)),
    }


def validate(path: Path, authority: Path | None = None) -> list[str]:
    file_data = path.read_bytes()
    core, errors = extract_core(file_data)
    if errors:
        return errors
    result = stats(core)
    expected = {
        "sha256": EXPECTED_SHA256,
        "characters": EXPECTED_CHARACTERS,
        "h2": EXPECTED_H2,
        "numbered": EXPECTED_NUMBERED_BEFORE_CHECKLIST,
        "checklist": EXPECTED_CHECKLIST,
        "tail": EXPECTED_UNNUMBERED_TAIL,
        "paragraphs": EXPECTED_PARAGRAPHS,
    }
    for key, value in expected.items():
        if result[key] != value:
            errors.append(f"核心正文 {key} 不一致：{result[key]}，应为 {value}")

    text = core.decode("utf-8")
    if not re.search(r"(?m)^> \*\*Seedance提示词不是剧本复述", text):
        errors.append("核心正文缺少最终加粗定义")

    if authority is not None:
        original = authority.read_bytes()
        if hashlib.sha256(original).hexdigest() != EXPECTED_SHA256:
            errors.append("指定的原始长规则文件不是已确认权威母版")
        original_lines = original.splitlines(keepends=True)
        final_lines = core.splitlines(keepends=True)
        if original_lines != final_lines:
            first = next(
                (index for index, pair in enumerate(zip(original_lines, final_lines), 1) if pair[0] != pair[1]),
                min(len(original_lines), len(final_lines)) + 1,
            )
            errors.append(f"逐行对比失败，首个差异位于第 {first} 行")
        if _paragraphs(original.decode("utf-8")) != _paragraphs(text):
            errors.append("逐段对比失败，存在删除、合并或改写段落")
        if original != core:
            errors.append("核心正文与原始长规则不是逐字节一致")
    return errors


def report(path: Path) -> dict[str, int | str | bool]:
    core, extraction_errors = extract_core(path.read_bytes())
    result = stats(core) if not extraction_errors else {
        "characters": 0, "h2": 0, "numbered": 0, "checklist": 0, "paragraphs": 0, "sha256": ""
    }
    exact = not extraction_errors and result["sha256"] == EXPECTED_SHA256
    return {
        "A_original_characters": EXPECTED_CHARACTERS,
        "B_final_core_characters": result["characters"],
        "C_original_h2": EXPECTED_H2,
        "D_final_core_h2": result["h2"],
        "E_original_numbered_rules": EXPECTED_NUMBERED_BEFORE_CHECKLIST,
        "F_final_numbered_rules": result["numbered"],
        "checklist_original": EXPECTED_CHECKLIST,
        "checklist_final": result["checklist"],
        "G_deleted_paragraph": not exact,
        "H_merged_paragraph": not exact,
        "I_rewritten_paragraph": not exact,
        "J_title_only_rule": not exact,
        "sha256": result["sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "references" / "seedance-prompt-rules-master.md",
    )
    parser.add_argument("--authority", type=Path)
    args = parser.parse_args()
    errors = validate(args.path, args.authority)
    values = report(args.path)
    labels = (
        ("A", "A_original_characters"), ("B", "B_final_core_characters"),
        ("C", "C_original_h2"), ("D", "D_final_core_h2"),
        ("E", "E_original_numbered_rules"), ("F", "F_final_numbered_rules"),
    )
    for label, key in labels:
        print(f"{label}. {values[key]}")
    for label, key in (
        ("G", "G_deleted_paragraph"), ("H", "H_merged_paragraph"),
        ("I", "I_rewritten_paragraph"), ("J", "J_title_only_rule"),
    ):
        print(f"{label}. {'是' if values[key] else '否'}")
    print(f"检查项：原始 {values['checklist_original']}，最终 {values['checklist_final']}")
    print(f"核心正文 SHA256：{values['sha256']}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: 核心正文逐字节、逐行、逐段完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
