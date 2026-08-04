#!/usr/bin/env python3
"""Validate Stage 3 mapping, segment integrity, categories, and deterministic approval."""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
from pathlib import Path
import re
import sys

from expand_stage3_fixed_prompts import TOKEN_RE, expand_text
from shotlist_common import HEADERS, find_tables, is_coverage_table, parse_duration, parse_frontmatter, read_text
from validate_stage2_fixed_prompts import category_map


STAGE3_HEADERS = ("镜号", "片段", "时长", "分镜内容", "Seedance提示词")
STAGE3_HEADERS_WITH_NOTES = STAGE3_HEADERS + ("备注",)
DOUBLE_BREAK_RE = re.compile(r"(?:<br\s*/?>){2}", re.I)
ANY_BREAK_RE = re.compile(r"<br\s*/?>", re.I)
NOTE_LINE_RE = re.compile(r"^(🔴|🟠|🟡|🔵)【([^】]+)】(.+)$")


def decimal_text(value: Decimal) -> str:
    if value == value.to_integral():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")


def scene_from_shot(shot: str) -> str:
    match = re.match(r"^(\d+)", shot.strip())
    return match.group(1) if match else ""


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def normalized_breaks(value: str) -> str:
    return ANY_BREAK_RE.sub("<br>", value.strip())


def prompt_blocks(prompt: str) -> tuple[list[str], bool]:
    normalized = normalized_breaks(prompt)
    protected = normalized.replace("<br><br>", "§§")
    has_single_break = "<br>" in protected
    return [item.strip() for item in normalized.split("<br><br>")], has_single_break


def note_lines(note: str) -> list[str]:
    normalized = ANY_BREAK_RE.sub("\n", note.strip())
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def source_shots(text: str) -> list[tuple[str, str, Decimal, str, str, str]]:
    result: list[tuple[str, str, Decimal, str, str, str]] = []
    for table in find_tables(text):
        if table.headers != HEADERS or is_coverage_table(table):
            continue
        for row in table.rows:
            result.append(
                (
                    row[0].strip(),
                    row[1].strip().upper(),
                    parse_duration(row[2]),
                    row[3].strip(),
                    row[4].strip(),
                    row[5].strip(),
                )
            )
    return result


def expected_storyboard(camera: str, picture: str, sound: str) -> str:
    return f"摄影机：{camera}<br><br>画面内容：{picture}<br><br>音效：{sound}"


def stage3_rows(
    text: str,
) -> tuple[list[tuple[str, str, Decimal, str, str, str]], list[str]]:
    errors: list[str] = []
    recognized = set(STAGE3_HEADERS_WITH_NOTES)
    tables = [table for table in find_tables(text) if recognized.intersection(table.headers)]
    if len(tables) != 1:
        return [], ["Stage 3 必须恰好包含一个五列表；存在阻塞、风险、待确认或提醒时才允许六列"]
    table = tables[0]
    if table.headers not in {STAGE3_HEADERS, STAGE3_HEADERS_WITH_NOTES}:
        return [], ["Stage 3 表头必须精确为 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词；仅有问题时末尾增加备注"]

    has_notes = table.headers == STAGE3_HEADERS_WITH_NOTES
    rows: list[tuple[str, str, Decimal, str, str, str]] = []
    any_note = False
    for line_no, row in zip(table.row_lines, table.rows):
        expected_length = len(table.headers)
        if len(row) != expected_length or any(not row[index].strip() for index in range(5)):
            errors.append(f"第 {line_no} 行：Stage 3 前五列不得留空，列数必须与表头一致")
            continue
        note = row[5].strip() if has_notes else ""
        if note:
            any_note = True
            for item in note_lines(note):
                match = NOTE_LINE_RE.fullmatch(item)
                if not match or not match.group(2).strip() or not match.group(3).strip():
                    errors.append(
                        f"第 {line_no} 行：备注每个问题必须逐行使用 🔴/🟠/🟡/🔵【类别】具体问题"
                    )
        try:
            duration = parse_duration(row[2])
        except ValueError as exc:
            errors.append(f"第 {line_no} 行：{exc}")
            continue
        rows.append(
            (
                row[0].strip().upper(),
                row[1].strip(),
                duration,
                row[3].strip(),
                row[4].strip(),
                note,
            )
        )
    if has_notes and not any_note:
        errors.append("没有任何问题时不得保留空的备注列")
    return rows, errors


def validate_mapping(
    source_text: str,
    stage2_texts: list[str],
    draft_text: str,
    approved_text: str | None = None,
) -> list[str]:
    errors: list[str] = []
    source_front = parse_frontmatter(source_text).values
    if source_front.get("status") != "approved":
        errors.append("Stage 3 的 Stage 1 权威源必须是 approved")

    categories: dict[str, str] = {}
    categories_by_scene: dict[str, dict[str, str]] = {}
    stage2_values_by_scene: dict[str, dict[str, str]] = {}
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    stage2_digests: set[str] = set()
    for text in stage2_texts:
        stage2_digests.add(hashlib.sha256(text.encode("utf-8")).hexdigest())
        mapping, stage_errors, values = category_map(text)
        errors.extend(stage_errors)
        scene = values.get("scene", "").strip()
        if values.get("status") != "approved":
            errors.append(f"场次 {scene or '?'} 的 Stage 2 必须整体 approved")
        if scene in categories_by_scene:
            errors.append(f"场次 {scene} 重复提供 Stage 2 文件")
        categories_by_scene[scene] = mapping
        stage2_values_by_scene[scene] = values
        if values.get("project") != source_front.get("project"):
            errors.append(f"场次 {scene} 的 Stage 2 project 与 Stage 1 不一致")
        if values.get("episode") != source_front.get("episode"):
            errors.append(f"场次 {scene} 的 Stage 2 episode 与 Stage 1 不一致")
        if values.get("stage1_sha256") != source_digest:
            errors.append(f"场次 {scene} 的 Stage 2 未绑定当前 Stage 1 摘要")
        if values.get("scene_image") and f"场景图：{values['scene_image']}" not in source_text:
            errors.append(f"场次 {scene} 的 Stage 2 场景图与 Stage 1 不一致")
        for name, value in mapping.items():
            if name in categories and categories[name] != value:
                errors.append(f"固定类别 {name} 在不同场次正文不一致；请使用场次专属名称")
            categories[name] = value

    draft_front = parse_frontmatter(draft_text).values
    if draft_front.get("artifact") != "horizontal-tv-stage3":
        errors.append("Stage 3 artifact 必须为 horizontal-tv-stage3")
    if draft_front.get("status") != "draft":
        errors.append("分类审核源必须是 Stage 3 draft")
    if draft_front.get("model") != "seedance-2.5":
        errors.append("当前自然动作与情感解析格式只允许 model: seedance-2.5")
    if draft_front.get("project") != source_front.get("project") or draft_front.get("episode") != source_front.get("episode"):
        errors.append("Stage 3 project/episode 必须与 Stage 1 一致")
    if draft_front.get("stage1_sha256") != source_digest:
        errors.append("Stage 3 draft 未绑定当前 Stage 1 摘要")
    declared_stage2_digests = {
        item.strip().lower()
        for item in re.split(r"[,，]", draft_front.get("stage2_sha256", ""))
        if item.strip()
    }
    if declared_stage2_digests != stage2_digests:
        errors.append("Stage 3 draft 未绑定全部且仅当前 approved Stage 2 摘要")
    source_stage1_names = {values.get("source_stage1") for values in stage2_values_by_scene.values()}
    if len(source_stage1_names) != 1 or draft_front.get("source_stage1") not in source_stage1_names:
        errors.append("Stage 3 与 Stage 2 的 source_stage1 不一致")

    expected = source_shots(source_text)
    expected_scenes = {scene for scene, _shot, _duration, _camera, _picture, _sound in expected}
    if set(categories_by_scene) != expected_scenes:
        missing = sorted(expected_scenes - set(categories_by_scene))
        extra = sorted(set(categories_by_scene) - expected_scenes)
        if missing:
            errors.append("缺少逐场 approved Stage 2：" + "、".join(missing))
        if extra:
            errors.append("Stage 2 含 Stage 1 不存在的场次：" + "、".join(extra))

    rows, row_errors = stage3_rows(draft_text)
    errors.extend(row_errors)
    actual_shots = [shot for shot, _segment, _duration, _storyboard, _prompt, _note in rows]
    expected_shots = [shot for _scene, shot, _duration, _camera, _picture, _sound in expected]
    if actual_shots != expected_shots:
        errors.append("Stage 3 镜号必须与 Stage 1 主序列完整、唯一且顺序一致")

    segment_durations: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
    segment_scenes: dict[int, set[str]] = defaultdict(set)
    prior_segment = 0
    elapsed = Decimal(0)
    for index, (shot, segment_text, duration, storyboard, prompt, note) in enumerate(rows):
        scene = scene_from_shot(shot)
        if not scene:
            errors.append(f"镜头 {shot} 必须以场次阿拉伯数字开头")
        elif index < len(expected) and scene != expected[index][0]:
            errors.append(f"镜头 {shot} 开头场次数字与 Stage 1 场次不一致")

        match = re.fullmatch(r"片段([1-9]\d*)", segment_text)
        if not match:
            errors.append(f"镜头 {shot} 的片段必须使用片段1格式")
            continue
        segment = int(match.group(1))
        if index == 0 and segment != 1:
            errors.append("片段编号必须从片段1开始")
        if prior_segment and segment not in {prior_segment, prior_segment + 1}:
            errors.append(f"镜头 {shot} 的片段编号不连续")
        if segment != prior_segment:
            elapsed = Decimal(0)
        if index and scene != scene_from_shot(rows[index - 1][0]) and segment == prior_segment:
            errors.append(f"场次变化为 {scene} 时必须开启新片段")

        segment_durations[segment] += duration
        segment_scenes[segment].add(scene)
        expected_start = elapsed
        expected_end = elapsed + duration
        elapsed = expected_end
        prior_segment = segment

        if index < len(expected):
            source_scene, _source_shot, source_duration, camera, source_picture, source_sound = expected[index]
            if duration != source_duration:
                errors.append(f"镜头 {shot} 时长与 Stage 1 不一致")
            expected_cell = expected_storyboard(camera, source_picture, source_sound)
            if normalized_breaks(storyboard) != normalized_breaks(expected_cell):
                errors.append(f"镜头 {shot} 的分镜内容必须逐字合并 Stage 1 摄影机、画面内容和音效")
            story_blocks, story_single = prompt_blocks(storyboard)
            if story_single or len(story_blocks) != 3:
                errors.append(f"镜头 {shot} 的分镜内容三类别之间必须空一行")
            if "剪辑：" in storyboard:
                errors.append(f"镜头 {shot} 的分镜内容不得包含 Stage 1 剪辑衔接")
        else:
            source_scene, camera, source_picture, source_sound = scene, "", "", ""

        start_text = decimal_text(expected_start)
        end_text = decimal_text(expected_end)
        expected_prefix = f"{start_text}-{end_text}秒。"
        if not prompt.startswith(expected_prefix):
            errors.append(f"镜头 {shot} 的提示词第一句必须为 {expected_prefix}")
        if "时长" in prompt[: max(20, len(expected_prefix) + 10)]:
            errors.append(f"镜头 {shot} 的提示词开头不得再写‘时长’")
        if not prompt.endswith("无配乐无字幕"):
            errors.append(f"镜头 {shot} 未精确以无配乐无字幕结尾")
        if prompt.count("无配乐无字幕") != 1:
            errors.append(f"镜头 {shot} 的无配乐无字幕必须且只能在末尾出现一次")
        if "剪辑：" in prompt:
            errors.append(f"镜头 {shot} 的 Seedance 提示词不得包含剪辑类别")
        if "保留人物真实皮肤质感与瑕疵" in prompt:
            errors.append(f"镜头 {shot} 使用了已废止的皮肤质量控制句")

        blocks, has_single_break = prompt_blocks(prompt)
        if has_single_break:
            errors.append(f"镜头 {shot} 的提示词类别之间必须空一行，不能只换一行")
        if len(blocks) not in {5, 6}:
            errors.append(f"镜头 {shot} 必须按摄影机、动作/表情、可选情感解析、声音、固定类别和质量控制分块")
        action_line = next((item for item in blocks if item.startswith("动作/表情：")), "")
        emotion_line = next((item for item in blocks if item.startswith("情感解析：")), "")
        sound_line = next((item for item in blocks if item.startswith("声音：")), "")
        fixed_line = next((item for item in blocks if item.startswith("固定类别：")), "")
        expected_labels = ["动作/表情：", "声音：", "固定类别："]
        if not all(any(item.startswith(label) for item in blocks) for label in expected_labels):
            errors.append(f"镜头 {shot} 缺少动作/表情、声音或固定类别独立块")
        if blocks:
            ordered = [0]
            for label in ("动作/表情：", "情感解析：", "声音：", "固定类别："):
                found = next((i for i, item in enumerate(blocks) if item.startswith(label)), None)
                if found is not None:
                    ordered.append(found)
            ordered.append(len(blocks) - 1)
            if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
                errors.append(f"镜头 {shot} 的提示词类别顺序错误")
        if emotion_line.strip() == "情感解析：N/A":
            errors.append(f"镜头 {shot} 不需要情感解析时必须省略整块")
        if source_picture and action_line and compact(source_picture) not in compact(action_line):
            errors.append(f"镜头 {shot} 的动作/表情未逐字保留 Stage 1 画面内容")

        tokens = TOKEN_RE.findall(prompt)
        for name in tokens:
            if name not in categories:
                errors.append(f"镜头 {shot} 使用未知固定类别 {name}")
            elif name not in categories_by_scene.get(scene, {}):
                errors.append(f"镜头 {shot} 使用了其他场次的固定类别 {name}")
        sound_tokens = TOKEN_RE.findall(sound_line)
        fixed_tokens = TOKEN_RE.findall(fixed_line)
        if any(name.startswith(("光影·", "背景·", "环境·", "群演·")) for name in sound_tokens):
            errors.append(f"镜头 {shot} 的视觉固定类别误放入声音块")
        if any(name.startswith(("基础音色·", "场次音色·", "阶段音色·", "场次声音·")) for name in fixed_tokens):
            errors.append(f"镜头 {shot} 的声音固定类别误放入固定类别块")
        if not fixed_tokens and fixed_line.strip() != "固定类别：无":
            errors.append(f"镜头 {shot} 没有视觉固定类别时必须写 固定类别：无")
        if fixed_tokens and "固定类别：无" in fixed_line:
            errors.append(f"镜头 {shot} 不得同时写固定类别与无")

        double_ranges = {
            "双人全景": "头部到脚部",
            "双人中景": "头部到腰部",
            "双人中近景": "头部到胸部",
            "双人近景": "头部到脖子",
        }
        for double_size, visible_range in double_ranges.items():
            if double_size in camera and visible_range not in blocks[0]:
                errors.append(f"镜头 {shot} 的 {double_size} 未写明两人{visible_range}可见")
        for shot_size in re.findall(r"(?:大全景|全景|中全景|中景|中近景|近景|大近景|极特写|特写)", camera):
            if shot_size not in blocks[0]:
                errors.append(f"镜头 {shot} 未保留 Stage 1 景别 {shot_size}")
        source_focals = re.findall(r"\d+(?:\.\d+)?\s*mm", camera, re.I)
        for focal in source_focals:
            if compact(focal).lower() not in compact(blocks[0]).lower():
                errors.append(f"镜头 {shot} 未保留 Stage 1 焦段 {focal}")
        if source_focals:
            compact_focal = compact(source_focals[0])
            if not re.match(
                rf"^{re.escape(expected_prefix)}{re.escape(compact_focal)}(?:，|,)",
                compact(blocks[0]),
                re.I,
            ):
                errors.append(f"镜头 {shot} 必须在累计时间后首先写焦段 {compact_focal}")
        if re.search(r"过.+[左右]肩拍", camera):
            for required_ots in ("前景", "虚化", "主体"):
                if required_ots not in blocks[0]:
                    errors.append(f"镜头 {shot} 的过肩提示词缺少{required_ots}说明")
        for token in ("左肩", "右肩", "左前方", "右前方", "左侧", "右侧", "眼平", "胸口高度", "腰部高度", "贴地", "平视", "略俯", "俯拍", "仰拍"):
            if token in camera and token not in prompt:
                errors.append(f"镜头 {shot} 未保留 Stage 1 摄影机要素 {token}")
        if "固定" in camera and "固定" not in blocks[0]:
            errors.append(f"镜头 {shot} 未保留固定镜头")
        for movement in ("推近", "拉远", "横移", "摇镜", "跟随", "升起", "下降", "环绕", "变焦", "手持"):
            if movement in camera and movement not in prompt:
                errors.append(f"镜头 {shot} 未保留 Stage 1 摄影机运动 {movement}")
        for sound_fact in [item.strip() for item in re.split(r"[；;。]", source_sound) if item.strip() and item.strip().upper() != "N/A"]:
            if compact(sound_fact) not in compact(sound_line):
                errors.append(f"镜头 {shot} 遗漏 Stage 1 声音：{sound_fact}")

        has_spoken_content = bool(re.search(r"[“\"][^”\"]+[”\"]|\b(?:O\.S\.|V\.O\.|VO)\b|(?:说|喊|叫|问|答道|低声|高声)[：:]", source_picture, re.I))
        voice_tokens = [name for name in sound_tokens if name.startswith(("基础音色·", "场次音色·", "阶段音色·"))]
        speakers = [
            item.strip()
            for item in re.split(r"[,，]", stage2_values_by_scene.get(scene, {}).get("speaking_characters", ""))
            if item.strip() and item.strip().lower() != "none"
        ]
        actual_speakers = [
            speaker
            for speaker in speakers
            if re.search(re.escape(speaker) + r".{0,30}(?:说|喊|叫|问|答道|低声|高声|[：:]).{0,10}[“\"]", source_picture)
        ]
        if has_spoken_content:
            if not any(name.startswith("基础音色·") for name in voice_tokens):
                errors.append(f"镜头 {shot} 有对白或明确发声但缺少基础音色类别")
            for speaker in actual_speakers:
                if f"基础音色·{speaker}" not in voice_tokens:
                    errors.append(f"镜头 {shot} 实际说话者 {speaker} 的基础音色类别错误")
        elif voice_tokens:
            errors.append(f"镜头 {shot} 没有说话却添加了完整音色类别")
        for fixed_prompt in categories_by_scene.get(scene, {}).values():
            if fixed_prompt and fixed_prompt in prompt:
                errors.append(f"镜头 {shot} 的 draft 提前混入完整固定提示词")

    for segment, total in segment_durations.items():
        if total > 30:
            errors.append(f"片段{segment}总时长 {total}s 超过30s硬上限")
    for segment, scenes in segment_scenes.items():
        if len(scenes) > 1:
            errors.append(f"片段{segment}跨越场次")
    for index, row in enumerate(rows):
        shot, segment_text, _duration, _storyboard, prompt, note = row
        first = index == 0 or rows[index - 1][1] != segment_text
        last = index == len(rows) - 1 or rows[index + 1][1] != segment_text
        if not first and any("【主体风险】" in item or "【主体阻塞】" in item for item in note_lines(note)):
            errors.append(f"镜头 {shot} 的片段级主体问题只能写在片段首镜备注")
        if first and not re.search(r"画面左|画面右|前景|后景|越过.+肩", prompt):
            errors.append(f"镜头 {shot} 是片段首镜但未自包含建立可见位置关系")
        if last and not re.search(r"落幅|最终|镜头结束|镜头停", prompt):
            errors.append(f"镜头 {shot} 是片段末镜但未写清可见最终状态")

    if approved_text is not None:
        if any(any(item.startswith("🔴") for item in note_lines(note)) for *_rest, note in rows):
            errors.append("Stage 3 draft 仍有🔴阻塞问题，不得生成 approved")
        expected_approved, expansion_errors = expand_text(draft_text, stage2_texts)
        errors.extend(expansion_errors)
        if approved_text != expected_approved:
            errors.append("Stage 3 approved 不是 draft 的固定类别机械展开结果")
        approved_front = parse_frontmatter(approved_text).values
        if approved_front.get("status") != "approved":
            errors.append("Stage 3 权威稿 status 必须为 approved")
        if TOKEN_RE.search(approved_text):
            errors.append("Stage 3 approved 中固定类别名称未完全消失")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_stage1", type=Path)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--stage2", type=Path, action="append", required=True)
    parser.add_argument("--approved", type=Path)
    args = parser.parse_args()
    errors = validate_mapping(
        read_text(args.source_stage1),
        [read_text(path) for path in args.stage2],
        read_text(args.draft),
        read_text(args.approved) if args.approved else None,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: Stage 1、Stage 2、Stage 3 draft/approved 映射与 Seedance 2.5 片段规则有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
