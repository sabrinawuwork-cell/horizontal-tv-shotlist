#!/usr/bin/env python3
"""Validate the V2 skill structure, Stage 3 approved output, and regressions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from shotlist_common import HEADERS as STAGE1_HEADERS, find_tables, is_coverage_table, parse_frontmatter as strict_frontmatter
from v2_delivery_checks import stage3_authority, mapping_errors
from v2_project_inputs import ProjectPolicy, inherited_policy, project_term_errors
from content_contract import (CATEGORY_NAMES, card_reference_errors, focal_lengths, mask_spans,
                              normalized_text, prompt_blocks, select_speech)


REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/v1-compatibility-contract.md",
    "references/output-schema.md",
    "references/stage1-camera-picture-writing.md",
    "references/v2-stage1-authorized-additions.md",
    "references/v2-authority-and-continuity.md",
    "references/v2-stage2-authorized-additions.md",
    "references/v2-stage2-fixed-prompts.md",
    "references/v2-stage2-schema.md",
    "references/v2-stage3-authorized-additions.md",
    "references/v2-stage3-schema.md",
    "references/v2-seedance-prompt-rules-master.md",
    "references/v2-model-seedance-2.5.md",
    "references/war-medical-drama-profile.md",
    "references/project-input-schema.md",
    "references/platform-safety-language.md",
    "references/continuity-checklist.md",
    "references/v2-continuity-additions.md",
    "references/v2-regression-cases.md",
    "scripts/validate_v1_compatibility.py",
    "scripts/validate_v2_prompts.py",
    "scripts/validate_v2_stage2.py",
    "scripts/validate_v2_shotlist.py",
    "scripts/v2_delivery_checks.py",
    "scripts/v2_project_inputs.py",
    "scripts/content_contract.py",
    "scripts/generation_tasks.py",
    "scripts/update_workbook.py",
    "scripts/shot_revision.py",
    "references/shot-revision.md",
    "references/generation-task-schema.md",
    "references/workbook-update.md",
    "evals/v2-regression-cases.json",
    "tests/test_v2_iteration.py",
    "tests/fixtures/v2-stage1-dialogue-approved.md",
    "tests/fixtures/v2-stage3-approved.md",
    "tests/fixtures/v2-stage2-no-voice-approved.md",
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
SHOT_RE = re.compile(r"^\d+([A-Z]+)(?:-\d+|-COV\d+)?$")
NORMALIZED_SHOT_RE = re.compile(
    r"^(?P<scene>\d+(?:-\d+)?)(?P<letters>[A-HJ-NP-Z]+)(?P<suffix>-\d+|-COV\d+)?$"
)
SHOT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"
VOICE_REFERENCE_RE = re.compile(r"【[^\n】]{0,40}音色参考：】")


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


def stage3_table(text: str) -> tuple[tuple[str, ...] | None, list[tuple[str, ...]], list[str]]:
    tables = [table for table in find_tables(text) if '镜号' in table.headers]
    if len(tables) != 1 or tables[0].headers not in STAGE3_HEADERS:
        return None, [], ['Stage 3 必须且只能有一张精确五列或条件式六列提示词表']
    table = tables[0]
    errors = []
    rows = []
    for line, row in zip(table.row_lines, table.rows):
        if len(row) != len(table.headers):
            errors.append(f'第{line}行：Stage 3 列数不符，不能跳过坏行')
            continue
        if any(not cell.strip() for cell in row[:5]):
            errors.append(f'第{line}行：Stage 3 前五列不得为空')
        rows.append(row)
    if len(table.headers) == 6 and not any(row[5].strip() for row in rows):
        errors.append('没有任何问题时不得保留空的备注列')
    return table.headers, rows, errors


def find_stage3_rows(text: str) -> tuple[tuple[str, ...] | None, list[tuple[str, ...]]]:
    header, rows, _errors = stage3_table(text)
    return header, rows


def remove_quotes(text: str) -> str:
    return QUOTE_RE.sub("", text)


def has_latin_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I))


def light_errors(prompt: str, *, lighting_mode: str = 'numeric') -> list[str]:
    errors: list[str] = []
    light_blocks = [block.body for block in prompt_blocks(prompt) if block.label == '光影']
    if not light_blocks:
        return errors
    light = '\n'.join(light_blocks)
    if lighting_mode == 'numeric' and not re.search(r"\d{3,4}\s*K", light, re.I):
        errors.append("光影缺少色温K")
    illuminance_values = len(re.findall(r"\d+(?:\.\d+)?(?:\s*[–—-]\s*\d+(?:\.\d+)?)?\s*lux", light, re.I))
    if lighting_mode == 'numeric' and illuminance_values < 2:
        errors.append("光影缺少人物亮面或暗面lux；背景按当前取景另行判断")
    # Counts certify numeric syntax only. Lighting logic and the use of
    # professional language belong to the existing scene-grounded review.

    return errors


def duplicate_errors(prompt: str) -> list[str]:
    # A duplicated structural prefix is a formatting error. Repeated sentences
    # or words alone do not establish a repeated action or sound event.
    return [f'类别名称重复：{block.label}：' for block in prompt_blocks(prompt)
            if block.label and block.body.startswith(block.label + '：')]


def prompt_errors(
    prompt: str, authorized: set[str], *, allow_voice_reference: bool = False,
    policy: ProjectPolicy | None = None,
) -> list[str]:
    """Check the declared container, not the meaning of its sentences."""
    errors: list[str] = []
    parsed = prompt_blocks(prompt)
    if not parsed:
        return ['提示词不能为空']
    errors.extend(card_reference_errors(prompt))
    counts = {name: sum(block.label == name for block in parsed) for name in CATEGORY_NAMES}
    for name in ('画面', '动作/表情', '声音'):
        if counts[name] != 1:
            errors.append(f'必须且只能包含一个{name}：块；各类别以空行分隔')
    if counts['情感解析'] > 1:
        errors.append('情感解析类别块重复')
    for block in parsed:
        if block.label and not block.body:
            errors.append(f'类别正文不得为空：{block.label}：')
        if block.label in {'固定类别', '剪辑', '音色', '音色状态'}:
            errors.append(f'Stage3不使用独立的{block.label}：类别；按当前输出规范组织')
    if not TIME_RE.match(parsed[0].text):
        errors.append('提示词未以片段内开始秒数-结束秒数开头')
    focals = focal_lengths(parsed[0].text)
    if not focals:
        errors.append('第一块缺少可读取的毫米焦段')
    elif any(value <= 0 for value in focals):
        errors.append('毫米焦段必须为正数')
    labels = [CATEGORY_NAMES.index(block.label) for block in parsed if block.label in CATEGORY_NAMES]
    if labels != sorted(labels):
        errors.append('提示词类别顺序错误')
    # Repeated visual category blocks may assemble several approved cards.
    # The authority check verifies their complete, unchanged bodies.
    if VOICE_REFERENCE_RE.search(remove_quotes(normalized_text(prompt))) and not allow_voice_reference:
        errors.append('默认输出不得出现【角色名音色参考：】块；只有当前任务明确启用时才允许')
    errors.extend(duplicate_errors(prompt))
    for block in parsed:
        if block.label == '光影':
            errors.extend(light_errors(block.text, lighting_mode=policy.lighting_mode if policy else 'numeric'))
    return errors


def prompt_policy_errors(prompt: str, speech: object, authorized: set[str], policy: ProjectPolicy) -> list[str]:
    """Apply the existing word policy to explicitly classified speech only."""
    parsed = prompt_blocks(prompt)
    actions = [block for block in parsed if block.label == '动作/表情']
    if len(actions) != 1:
        return []  # Container errors are already reported by prompt_errors.
    values, spans, errors = select_speech(actions[0].body, speech, '提示词实际发声')
    if errors:
        return errors
    spoken = '\n'.join(values)
    other = '\n\n'.join(mask_spans(block.body, spans) if block is actions[0] else block.text
                         for block in parsed)
    authorized = authorized | set(policy.dialogue_sensitive_authorizations)
    errors.extend(project_term_errors(other, spoken, policy, authorized))
    for term in SENSITIVE_ZH:
        if term in other:
            errors.append(f'非对白敏感词未处理：{term}')
        if term in spoken and term not in authorized:
            errors.append(f'对白敏感词缺少授权：{term}')
    for term in SENSITIVE_LATIN:
        if has_latin_term(other, term):
            errors.append(f'非对白敏感词未处理：{term}')
        if has_latin_term(spoken, term) and term.upper() not in {value.upper() for value in authorized}:
            errors.append(f'对白敏感词缺少授权：{term}')
    return errors


def normalized_shot_sequence_errors(shots: list[str]) -> list[str]:
    errors: list[str] = []
    parsed: list[tuple[str, str, str | None]] = []
    for shot in shots:
        match = NORMALIZED_SHOT_RE.fullmatch(shot)
        if not match:
            errors.append(f"镜号格式不符合场次加连续字母规则：{shot}")
            continue
        parsed.append((match.group("scene"), match.group("letters"), match.group("suffix")))
    if errors:
        return errors
    if len(set(shots)) != len(shots):
        errors.append("主序列镜号存在重复")

    scene_order: list[str] = []
    scene_letters: dict[str, list[tuple[str, str | None]]] = {}
    for scene, letters, suffix in parsed:
        if scene not in scene_letters:
            scene_order.append(scene)
            scene_letters[scene] = []
        elif scene_order[-1] != scene:
            errors.append(f"场次 {scene} 的镜号没有保持连续分组")
        scene_letters[scene].append((letters, suffix))

    def rank(letters: str) -> int:
        value = 0
        for letter in letters:
            value = value * len(SHOT_ALPHABET) + SHOT_ALPHABET.index(letter) + 1
        return value

    for scene in scene_order:
        items = scene_letters[scene]
        temporary = [f"{scene}{letters}{suffix}" for letters, suffix in items if suffix]
        if temporary:
            errors.append(f"场次 {scene} 仍含未归一化的临时后缀镜号：{'、'.join(temporary)}")
        actual = [rank(letters) for letters, _suffix in items]
        expected = list(range(1, len(items) + 1))
        if actual != expected:
            errors.append(f"场次 {scene} 主序列镜号未按行序从A连续递进并跳过I/O")
    return errors


def checked_stage3(
    path: Path,
    *,
    stage1_path: Path | None = None,
    allow_voice_reference: bool = False,
) -> tuple[list[str], list[tuple[str, ...]], dict]:
    try:
        text = read_text(path)
    except (OSError, UnicodeError) as exc:
        return [f'Stage 3 文件无法读取：{exc}'], [], {}
    errors: list[str] = []
    parsed_front = strict_frontmatter(text)
    errors.extend(parsed_front.errors)
    front = parsed_front.values
    policy_errors, policy = inherited_policy(path)
    errors.extend(policy_errors)
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
    without_quotes = remove_quotes(re.sub(r'<br\s*/?>', '\n', text, flags=re.I))
    if front.get('status') == 'approved' and ('🔴' in front.get('blocking_issues', '') or re.search(r'(?:^|[|\n])\s*(?:[-*]\s*)?🔴', without_quotes)):
        errors.append('approved Stage 3 不得含明确的🔴阻塞问题')
    header, rows, table_errors = stage3_table(text)
    errors.extend(table_errors)
    if header is None:
        return errors + ["没有找到精确五列或条件式六列Stage 3表"], [], {}
    if not rows:
        errors.append("Stage 3表没有镜头行")
    if front.get('status') == 'approved' and any(len(row) == 6 and '🔴' in remove_quotes(row[5]) for row in rows):
        errors.append('approved Stage 3 不得含明确的🔴阻塞问题')
    # Numbering is validated on the actual Stage 1 below, then its entire
    # sequence must match. Stage 3 cannot declare its own numbering exception.
    authorized = {
        item.strip()
        for item in front.get("dialogue_sensitive_authorizations", "").split(",")
        if item.strip() and item.strip().lower() != "none"
    }
    for row_number, row in enumerate(rows, 1):
        shot = row[0] or f"第{row_number}行"
        for error in prompt_errors(row[4], authorized, allow_voice_reference=allow_voice_reference, policy=policy):
            errors.append(f"镜头{shot}：{error}")

    sources: dict = {}
    try:
        authority_errors, sources = stage3_authority(path, rows, stage1_path)
        errors.extend(authority_errors)
        errors.extend(mapping_errors(rows, sources))
        for row in rows:
            binding = sources.get('_bindings', {}).get(row[0].upper(), {})
            speech = binding.get('speech') if isinstance(binding, dict) else None
            errors.extend(f'镜头{row[0]}：{issue}' for issue in
                          prompt_policy_errors(row[4], speech, authorized, policy))
        # Task self-sufficiency is judged by receiving the actual prompt and
        # references in the existing content review, not by forbidden phrases.
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(f'V2 权威来源无法校验：{exc}')
    return list(dict.fromkeys(errors)), rows, sources


def validate_stage3(
    path: Path, *, stage1_path: Path | None = None, allow_voice_reference: bool = False,
) -> list[str]:
    errors, _, sources = checked_stage3(path, stage1_path=stage1_path,
                                      allow_voice_reference=allow_voice_reference)
    print_content_warnings(sources)
    return errors


def print_content_warnings(sources: dict) -> None:
    for warning in sources.get('_content_warnings', []):
        print(f"WARNING: {warning['message']}\n  Stage1：{warning['source_camera']}"
              f"\n  Stage3：{warning['prompt_camera']}", file=sys.stderr)


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
    routed_references = [relative for relative in REQUIRED_FILES if relative.startswith("references/")]
    for relative in routed_references:
        if relative not in skill and relative not in {
            "references/output-schema.md",
            "references/stage1-camera-picture-writing.md",
            "references/continuity-checklist.md",
        }:
            errors.append(f"SKILL.md没有直接引用：{relative}")
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
        authorized = set(str(item) for item in case.get("authorized", []))
        issues = prompt_errors(sample, authorized,
                               allow_voice_reference=bool(case.get("voice_reference_authorized", False)))
        if 'speech' in case:
            issues.extend(prompt_policy_errors(sample, case['speech'], authorized, ProjectPolicy()))
        valid = not issues
    elif kind == "segment_boundary":
        return False, '任务自足性需要内容审读，不能从短语或本函数得到通过结论'
    elif kind == "conditional_categories":
        values = dict(raw_sample) if isinstance(raw_sample, dict) else {}
        pure_function = bool(values.get("pure_function", False))
        extras_visible = bool(values.get("extras_visible", False))
        has_emotion = bool(values.get("has_emotion", False))
        has_extras = bool(values.get("has_extras", False))
        # Independent performance permits this optional block; it does not
        # require it. Purely functional shots still omit emotional analysis.
        emotion_valid = not (pure_function and has_emotion)
        extras_valid = not (has_extras and not extras_visible)
        valid = emotion_valid and extras_valid
    elif kind == "frontmatter":
        front = dict(raw_sample) if isinstance(raw_sample, dict) else {}
        required_keys = ("project", "episode", "source_stage1", "source_stage2")
        valid = all(front.get(key) for key in required_keys) and not any(
            token in str(front.get(key, ""))
            for key in required_keys
            for token in ("本次项目", "所属集", "待定", "approved（")
        )
    elif kind == "shot_numbers":
        shots = [str(item) for item in raw_sample] if isinstance(raw_sample, list) else []
        matches = [SHOT_RE.fullmatch(shot) for shot in shots]
        valid = bool(shots) and all(matches) and all(
            "I" not in match.group(1) and "O" not in match.group(1)
            for match in matches
            if match is not None
        )
        for pair in case.get("required_pairs", []):
            left, right = [str(item) for item in pair]
            valid = valid and any(a == left and b == right for a, b in zip(shots, shots[1:]))
    elif kind == "normalized_shot_sequence":
        shots = [str(item) for item in raw_sample] if isinstance(raw_sample, list) else []
        valid = bool(shots) and not normalized_shot_sequence_errors(shots)
    elif kind == "camera_format":
        # This legacy fixture now checks only explicit numeric syntax. It does
        # not certify the meaning/completeness of a camera description.
        values = focal_lengths(sample)
        valid = bool(values) and all(value > 0 for value in values)
    else:
        return False, f"未知回归类型：{kind}"
    expected = bool(case.get("expected_valid", True))
    return valid == expected, f"实际valid={valid}，期望valid={expected}"


def run_self_tests(root: Path) -> list[str]:
    path = root / "evals/v2-regression-cases.json"
    cases = json.loads(read_text(path))
    errors: list[str] = []
    for case in cases:
        if case.get('kind') == 'segment_boundary':
            print(f"UNASSESSED: {case.get('id', '?')}需要实际任务内容审读，未计入自动通过", file=sys.stderr)
            continue
        passed, detail = evaluate_case(case)
        if not passed:
            errors.append(f"{case.get('id', '?')}失败：{detail}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--stage3", type=Path)
    parser.add_argument("--stage1", type=Path)
    parser.add_argument("--allow-voice-reference", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = (args.skill_root or Path(__file__).resolve().parents[1]).resolve()
    errors: list[str] = []
    if args.skill_root or not args.stage3:
        errors.extend(validate_structure(root))
    if args.stage3:
        errors.extend(
            validate_stage3(
                args.stage3.resolve(),
                stage1_path=args.stage1.resolve() if args.stage1 else None,
                allow_voice_reference=args.allow_voice_reference,
            )
        )
    if args.self_test:
        errors.extend(run_self_tests(root))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    checked = []
    if args.skill_root or not args.stage3:
        checked.append('技能结构')
    if args.stage3:
        checked.append('Stage 3 来源、映射和格式')
    if args.self_test:
        checked.append('指定自动回归（不含标记为UNASSESSED的内容审读项）')
    print('OK: V2 ' + '、'.join(checked) + '检查通过；不含空间语义或视频生成验收')
    return 0


if __name__ == "__main__":
    sys.exit(main())
