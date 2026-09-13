#!/usr/bin/env python3
"""Fail when V2 changes any inherited V1 file outside the approved overlay surface."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SKIP_PARTS = {".git", "dist", "__pycache__", "horizontal-tv-shotlist-v2"}
ALLOWED_CHANGED = {Path("SKILL.md"), Path("agents/openai.yaml")}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inherited_files(root: Path) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        result[rel] = path
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()

    candidate = args.candidate.resolve()
    manifest = candidate / "references/v1-format-baseline.json"
    payload = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    authorized = payload.get("authorized_method_updates", {}).get("files", {})
    if args.baseline:
        baseline = args.baseline.resolve()
        if not baseline.is_dir():
            print(f"ERROR: V1 baseline not found: {baseline}")
            return 2
        expected = {
            rel: digest(source)
            for rel, source in inherited_files(baseline).items()
            if rel not in ALLOWED_CHANGED
        }
    else:
        if not manifest.is_file():
            print(f"ERROR: embedded format baseline not found: {manifest}")
            return 2
        expected = {Path(rel): value for rel, value in payload["files"].items()}
        if not expected:
            print("ERROR: embedded format baseline is empty")
            return 2

    errors: list[str] = []
    for rel, expected_digest in sorted(expected.items()):
        target = candidate / rel
        if not target.is_file():
            errors.append(f"missing inherited file: {rel}")
            continue
        current_digest = digest(target)
        if expected_digest != current_digest and authorized.get(rel.as_posix()) != current_digest:
            errors.append(f"unauthorized inherited-file change: {rel}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    output_schema = (candidate / "references/output-schema.md").read_text(encoding="utf-8")
    skill = (candidate / "SKILL.md").read_text(encoding="utf-8")

    required_output = [
        "场次 | 镜号 | 时长 | 摄影机 | 画面内容 | 音效 | 剪辑衔接",
        "镜号使用场次加字母，跳过`I/O`。插镜使用`3B-1`，覆盖使用`3B-COV1`。",
        "`A的近景，85mm，机位在A右前方，眼平固定镜头。`",
        "过B的左肩拍A的中景",
        "POV后接当前实际景别",
    ]
    for text in required_output:
        if text not in output_schema:
            errors.append(f"Stage 1 exact output contract missing: {text}")

    # Approved camera-writing prose is protected by the versioned digest.
    # Requiring an obsolete sentence would reject authorized equivalent wording;
    # executable field constraints belong to the public shotlist validator.

    required_skill = [
        "references/v1-compatibility-contract.md",
        "references/v2-stage1-authorized-additions.md",
        "references/v2-stage2-authorized-additions.md",
        "references/v2-stage3-authorized-additions.md",
        "直接生成 Stage 3 `approved`",
    ]
    for text in required_skill:
        if text not in skill:
            errors.append(f"V2 routing requirement missing from SKILL.md: {text}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    revised = sum(digest(candidate / rel) != value for rel, value in expected.items())
    print(f"PASS: {len(expected)} baseline files verified ({len(expected)-revised} unchanged, {revised} authorized method revisions)")
    print("PASS: Stage 1 column and shot-number declarations verified; camera semantics require content review")
    print("PASS: current V2 routing is present; this check does not assess directing quality")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
