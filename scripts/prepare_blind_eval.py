#!/usr/bin/env python3
"""Randomize candidate Markdown files and keep identity keys private."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import tempfile


LABELS = "XYZWVUTSRQPONMLKJHGFEDCBA"
IDENTITY_KEYS = {"candidate", "generator", "skill", "version", "revision_note"}


def parse_candidate(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("candidate must be NAME=PATH")
    name, raw_path = value.split("=", 1)
    path = Path(raw_path).expanduser().resolve()
    if not name.strip() or not path.is_file():
        raise argparse.ArgumentTypeError(f"invalid candidate: {value}")
    return name.strip(), path


def sanitize(text: str, label: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if closing is not None:
            kept = ["---"]
            kept.extend(
                line
                for line in lines[1:closing]
                if line.split(":", 1)[0].strip().lower() not in IDENTITY_KEYS
            )
            kept.append("---")
            lines = kept + lines[closing + 1 :]
    heading = next((i for i, line in enumerate(lines) if re.match(r"^#\s+", line)), None)
    if heading is None:
        lines = [f"# Candidate {label}", ""] + lines
    else:
        lines[heading] = f"# Candidate {label}"
    return "\n".join(lines).rstrip() + "\n"


def prepare(case: str, candidates: list[tuple[str, Path]], output: Path, seed: str | None) -> Path:
    if len(candidates) < 2:
        raise ValueError("at least two candidates are required")
    if len(candidates) > len(LABELS):
        raise ValueError("too many candidates")
    shuffled = list(candidates)
    random.Random(seed).shuffle(shuffled)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{case}-", dir=output.parent))
    blinded = staging / "blinded"
    private = staging / "private"
    blinded.mkdir()
    private.mkdir()
    key = {"case_id": case, "seed_recorded": seed is not None, "labels": {}}
    for label, (name, source) in zip(LABELS, shuffled):
        raw = source.read_bytes()
        clean = sanitize(raw.decode("utf-8-sig"), label)
        (blinded / f"{case}-candidate-{label}.md").write_text(clean, encoding="utf-8")
        key["labels"][label] = {
            "candidate": name,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "blinded_sha256": hashlib.sha256(clean.encode()).hexdigest(),
        }
    (private / f"{case}-key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(Path(__file__).resolve().parents[1] / "evals" / "rubric.md", blinded / "RUBRIC.md")
    if output.exists():
        shutil.rmtree(output)
    staging.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--candidate", action="append", required=True, type=parse_candidate)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed")
    args = parser.parse_args()
    print(prepare(args.case, args.candidate, args.output, args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
