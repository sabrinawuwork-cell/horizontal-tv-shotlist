#!/usr/bin/env python3
"""Validate the V2 skill structure, Stage 3 approved output, and regressions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/stage1-directing-rules.md",
    "references/stage2-fixed-prompt-rules.md",
    "references/stage3-seedance-rules.md",
    "references/war-medical-drama-profile.md",
    "references/project-input-schema.md",
    "references/platform-safety-language.md",
    "references/continuity-checklist.md",
    "references/regression-cases.md",
    "scripts/validate_prompts.py",
    "evals/regression-cases.json",
)

STAGE3_HEADERS = (
    ("镜号", "片段", "时长", "分镜内容", "Seedance提示词"),
    ("镜号", "片段", "时长", "分镜内容", "Seedance提示词", "备注"),
)

SENSITIVE_ZH = ("战场", "红军", "革命", "小孩子", "出血", "血迹")
SENSITIVE_LATIN = ("AV", "3P")
CATEGORY_LABELS = (
    "画面：",
    "动作/表情：",
    "情感解析：",
    "声音：",
    "光影：",
    "背景：",
    "环境：",
    "群演：",
    "外观：",
)
QUOTE_RE = re.compile(r"“[^”]*”|‘[^’]*’|\"[^\"]*\"")
TIME_RE = re.compile(r"^\d+(?:\.\d+)?-\d+(?:\.\d+)?秒。")
MM_RE = re.compile(r"(?<!\d)\d{2,3}mm(?!\w)", re.I)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def split_markdown_row(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def find_stage3_rows(text: str) -> tuple[tuple[str, ...] | None, list[tuple[str, ...]]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        header = split_markdown_row(line)
        if header not in STAGE3_HEADERS:
            continue
        rows: list[tuple[str, ...]] = []
        for raw in lines[index + 2 :]:
            if not raw.lstrip().startswith("|"):
                break
            row = split_markdown_row(raw)
            if len(row) == len(header):
                rows.append(row)
        return header, rows
    return None, []


def quoted_terms(text: str) -> str:
    return "\n".join(match.group(0) for match in QUOTE_RE.finditer(text))


def remove_quotes(text: str) -> str:
    return QUOTE_RE.sub("", text)


def has_latin_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I))


def light_errors(prompt: str) -> list[str]:
    errors: list[str] = []
    if "光影：" not in prompt:
        return errors
    light = prompt.split("光影：", 1)[1]
    light = re.split(r"(?:<br><br>|\n\n)(?:背景|环境|群演|音色)：", light, maxsplit=1)[0]
    if not re.search(r"\d{3,4}\s*K", light, re.I):
        errors.append("光影缺少色温K")
    if len(re.findall(r"\d+(?:\.\d+)?(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*lux", light, re.I)) < 3:
        errors.append("光影缺少亮面、暗面或背景lux")
    if not any(token in light for token in ("左侧", "右侧", "左脸", "右脸", "左右两侧")):
        errors.append("光影缺少人物具体受光侧")
    if not any(token in light for token in ("主光", "煤油灯", "篝火", "窗", "灯", "火焰")):
        errors.append("光影缺少具体逻辑光源")
    if any(token in light for token in ("保持far-side key", "保持 far-side key", "远离摄影机的一侧是亮面", "靠近摄影机的一侧落入阴影")):
        errors.append("光影使用抽象far-side元语言")
    return errors


def duplicate_errors(prompt: str) -> list[str]:
    errors: list[str] = []
    for label in CATEGORY_LABELS:
        if f"{label}{label}" in prompt:
            errors.append(f"类别名称重复：{label}")
    normalized = prompt.replace("<br><br>", "\n\n").replace("<br>", "\n")
    sentences = [item.strip() for item in re.split(r"[。；!?！？]", normalized) if len(item.strip()) >= 8]
    for previous, current in zip(sentences, sentences[1:]):
        if previous == current:
            errors.append("存在连续重复句")
            break
    return errors


def blank_line_errors(prompt: str) -> list[str]:
    errors: list[str] = []
    normalized = prompt.replace("<br><br>", "\n\n").replace("<br />", "\n").replace("<br>", "\n")
    for label in CATEGORY_LABELS:
        start = 0
        while True:
            index = normalized.find(label, start)
            if index < 0:
                break
            if index > 0 and normalized[max(0, index - 2) : index] != "\n\n":
                errors.append(f"{label}前缺少空行")
            start = index + len(label)
    return errors


def prompt_errors(prompt: str, authorized: set[str]) -> list[str]:
    errors: list[str] = []
    normalized = prompt.replace("<br><br>", "\n\n").replace("<br />", "\n").replace("<br>", "\n").strip()
    if not TIME_RE.match(normalized):
        errors.append("提示词未以片段内开始秒数-结束秒数开头")
    if len(MM_RE.findall(normalized.split("\n\n", 1)[0])) != 1:
        errors.append("第一块必须且只能包含一个具体毫米焦段")
    if not normalized.endswith("无配乐无字幕"):
        errors.append("提示词未精确以无配乐无字幕结束")
    if normalized.count("无配乐无字幕") != 1:
        errors.append("无配乐无字幕必须且只能出现一次")
    if "固定类别：" in normalized or re.search(r"〔[^〕]+·[^〕]+〕", normalized):
        errors.append("Stage 3 approved不得包含固定类别元标签或占位符")
    if "剪辑：" in normalized:
        errors.append("Stage 3提示词不得包含剪辑类别")
    if re.search(r"(?:位于|站在)[^。；\n]{0,30}画外|看向画外的", normalized):
        errors.append("镜外角色不得进入视觉站位或视线对象描述")
    if re.search(r"(?:^|\n\n)音色：", normalized):
        errors.append("音色必须合入声音类别，不得建立独立音色块")
    order = ("画面：", "动作/表情：", "情感解析：", "声音：", "光影：", "背景：", "环境：", "群演：", "外观：")
    positions = [normalized.find(label) for label in order]
    present_positions = [position for position in positions if position >= 0]
    if present_positions != sorted(present_positions):
        errors.append("提示词类别顺序错误")
    errors.extend(blank_line_errors(prompt))
    errors.extend(duplicate_errors(prompt))
    errors.extend(light_errors(prompt))

    quotes = quoted_terms(normalized)
    non_dialogue = remove_quotes(normalized)
    for term in SENSITIVE_ZH:
        if term in non_dialogue:
            errors.append(f"非对白敏感词未处理：{term}")
        if term in quotes and term not in authorized:
            errors.append(f"对白敏感词缺少授权：{term}")
    for term in SENSITIVE_LATIN:
        if has_latin_term(non_dialogue, term):
            errors.append(f"非对白敏感词未处理：{term}")
        if has_latin_term(quotes, term) and term.upper() not in {item.upper() for item in authorized}:
            errors.append(f"对白敏感词缺少授权：{term}")
    return errors


def validate_stage3(path: Path) -> list[str]:
    text = read_text(path)
    errors: list[str] = []
    front = parse_frontmatter(text)
    if front.get("status") != "approved":
        errors.append("Stage 3 V2必须直接标记status: approved")
    required_front = (
        "artifact",
        "project",
        "episode",
        "version",
        "status",
        "source_stage1",
        "source_stage2",
        "model",
        "segment_hard_limit",
        "dialogue_sensitive_authorizations",
    )
    for key in required_front:
        if not front.get(key):
            errors.append(f"Stage 3 frontmatter缺少{key}")
    if front.get("artifact") != "horizontal-tv-stage3-v2":
        errors.append("Stage 3 artifact不正确")
    if front.get("model", "").lower() != "seedance-2.5":
        errors.append("Stage 3 model必须是seedance-2.5")
    if front.get("segment_hard_limit") != "30s":
        errors.append("Stage 3 segment_hard_limit必须是30s")
    for key in ("project", "episode", "source_stage1", "source_stage2"):
        if any(token in front.get(key, "") for token in ("本次项目", "所属集", "待定", "approved（")):
            errors.append(f"Stage 3 frontmatter字段{key}不得使用说明性占位符")
    if re.search(r"(?m)^status:\s*draft\s*$", text):
        errors.append("Stage 3 V2不得存在draft状态")
    header, rows = find_stage3_rows(text)
    if header is None:
        return errors + ["没有找到精确五列或条件式六列Stage 3表"]
    if not rows:
        errors.append("Stage 3表没有镜头行")
    authorized = {
        item.strip()
        for item in front.get("dialogue_sensitive_authorizations", "").split(",")
        if item.strip() and item.strip().lower() != "none"
    }
    for row_number, row in enumerate(rows, 1):
        shot = row[0] or f"第{row_number}行"
        for error in prompt_errors(row[4], authorized):
            errors.append(f"镜头{shot}：{error}")
    return errors


def validate_structure(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"缺少文件：{relative}")
    skill_path = root / "SKILL.md"
    if not skill_path.is_file():
        return errors
    skill = read_text(skill_path)
    if "TODO" in skill:
        errors.append("SKILL.md仍含TODO")
    front = parse_frontmatter(skill)
    if set(front) != {"name", "description"}:
        errors.append("SKILL.md frontmatter只能包含name和description")
    if front.get("name") != "horizontal-tv-shotlist-v2":
        errors.append("SKILL.md名称不正确")
    for relative in REQUIRED_FILES[2:10]:
        link = relative.replace("references/", "references/")
        if link not in skill:
            errors.append(f"SKILL.md没有直接引用：{link}")
    if "Stage 3 `approved`" not in skill or "不生成 Stage 3 draft" not in skill:
        errors.append("SKILL.md没有明确Stage 3直接approved门禁")
    runtime_dependency = re.search(r"\.\./horizontal-tv-shotlist(?:/|`)", skill)
    if runtime_dependency:
        errors.append("V2不得依赖V1运行时文件")
    return errors


def evaluate_case(case: dict[str, object]) -> tuple[bool, str]:
    kind = str(case["kind"])
    raw_sample = case.get("sample", "")
    sample = str(raw_sample)
    required = [str(item) for item in case.get("required", [])]
    forbidden = [str(item) for item in case.get("forbidden", [])]

    if kind == "presence":
        valid = all(item in sample for item in required) and not any(item in sample for item in forbidden)
    elif kind == "ordered":
        positions = [sample.find(item) for item in required]
        valid = all(position >= 0 for position in positions) and positions == sorted(positions)
    elif kind == "light":
        valid = not light_errors(sample)
    elif kind == "prompt":
        valid = not prompt_errors(sample, set(str(item) for item in case.get("authorized", [])))
    elif kind == "shot_scale":
        early = [str(item) for item in case.get("early", [])]
        late = [str(item) for item in case.get("late", [])]
        valid = not any("85mm" in item or "100mm" in item for item in early) and any(
            "85mm" in item or "100mm" in item for item in late
        )
    elif kind == "frontmatter":
        front = dict(raw_sample) if isinstance(raw_sample, dict) else {}
        required_keys = ("project", "episode", "source_stage1", "source_stage2")
        valid = all(front.get(key) for key in required_keys) and not any(
            token in str(front.get(key, ""))
            for key in required_keys
            for token in ("本次项目", "所属集", "待定", "approved（")
        )
    else:
        return False, f"未知回归类型：{kind}"
    expected = bool(case.get("expected_valid", True))
    return valid == expected, f"实际valid={valid}，期望valid={expected}"


def run_self_tests(root: Path) -> list[str]:
    path = root / "evals/regression-cases.json"
    cases = json.loads(read_text(path))
    errors: list[str] = []
    for case in cases:
        passed, detail = evaluate_case(case)
        if not passed:
            errors.append(f"{case.get('id', '?')}失败：{detail}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--stage3", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = (args.skill_root or Path(__file__).resolve().parents[1]).resolve()
    errors: list[str] = []
    if args.skill_root or not args.stage3:
        errors.extend(validate_structure(root))
    if args.stage3:
        errors.extend(validate_stage3(args.stage3.resolve()))
    if args.self_test:
        errors.extend(run_self_tests(root))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: horizontal-tv-shotlist-v2 structure, Stage 3 rules, and regressions are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
