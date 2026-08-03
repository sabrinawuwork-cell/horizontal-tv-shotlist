#!/usr/bin/env python3
"""Build a clean installable skill package without tests or evaluation data."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tempfile


INCLUDED_DIRECTORIES = ("agents", "references", "scripts", "examples")
EXCLUDED = {
    "__pycache__",
    ".DS_Store",
    "build_distribution.py",
    "prepare_blind_eval.py",
    "node_modules",
    # Source-only compatibility or calibration references. Runtime rules are
    # routed explicitly from SKILL.md; shipping these duplicates can reintroduce
    # stale wording or create a second authority.
    "output-contract.md",
    "prompt-conversion.md",
    "research-and-calibration.md",
    "technical-continuity.md",
}


def include(path: Path) -> bool:
    return not any(part in EXCLUDED for part in path.parts)


def build(source: Path, destination: Path) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="horizontal-tv-shotlist-", dir=destination.parent))
    package = staging / "horizontal-tv-shotlist"
    package.mkdir()
    shutil.copy2(source / "SKILL.md", package / "SKILL.md")
    for directory in INCLUDED_DIRECTORIES:
        src_dir = source / directory
        if not src_dir.exists():
            continue
        for item in src_dir.rglob("*"):
            relative = item.relative_to(src_dir)
            if not include(relative) or item.is_dir():
                continue
            target = package / directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    if destination.exists():
        shutil.rmtree(destination)
    package.replace(destination)
    shutil.rmtree(staging, ignore_errors=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source, args.output)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
