#!/usr/bin/env python3
"""Validate the horizontal nine-column Markdown authority."""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

from shotlist_common import (
    HEADERS,
    find_tables,
    format_decimal,
    is_coverage_table,
    parse_duration,
    parse_frontmatter,
    parse_shot_id,
    read_text,
    require_fields,
    shot_rank,
)


REQUIRED_FRONTMATTER = (
    "artifact",
    "project",
    "episode",
    "version",
    "status",
    "source",
    "aspect_ratio",
    "shooting_language",
    "production_profile",
    "total_duration",
    "blocking_issues",
    "long_shot_authorizations",
    "active_cast_authorizations",
    "scene_image_authorizations",
)


def _authorization_set(value: str) -> set[str]:
    if not value or value.strip().lower() == "none":
        return set()
    return {item.strip().upper() for item in re.split(r"[,，]", value) if item.strip()}


def _horizontal_ratio(value: str) -> bool:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", value)
    return bool(match and Decimal(match.group(1)) > Decimal(match.group(2)))


def _declared_scene_durations(text: str) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    current_scene: str | None = None
    for line in text.splitlines():
        heading = re.match(r"^#{2,4}\s*场次\s+(.+?)\s*$", line.strip())
        if heading:
            current_scene = heading.group(1).strip()
            continue
        match = re.search(r"场次时长\s*[:：]\s*(\d+(?:\.\d+)?)\s*(?:s|秒)", line)
        if match and current_scene:
            result[current_scene] = Decimal(match.group(1))
    return result


def _scene_notes(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    current_scene: str | None = None
    for line in text.splitlines():
        heading = re.match(r"^#{2,4}\s*场次\s+(.+?)\s*$", line.strip())
        if heading:
            current_scene = heading.group(1).strip()
            result.setdefault(current_scene, {})
            continue
        if not current_scene:
            continue
        match = re.match(
            r"^\s*[-*]\s*(场景图|基础全景机位与主轴线|far-side key基准)\s*[:：]\s*(.+?)\s*$",
            line,
            re.I,
        )
        if match:
            result[current_scene][match.group(1).lower()] = match.group(2).strip()
    return result


def _load_dialogue_manifest(path: Path | None) -> list[str]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("dialogue", payload) if isinstance(payload, dict) else payload
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError("对白清单必须是字符串数组或包含 dialogue 字符串数组的对象")
    return values


def validate_shotlist_text(
    text: str, *, dialogue_manifest: Path | None = None, asset_root: Path | None = None
) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    frontmatter = parse_frontmatter(text)
    errors.extend(frontmatter.errors)
    errors.extend(require_fields(frontmatter.values, REQUIRED_FRONTMATTER))
    values = frontmatter.values
    long_shot_authorizations = _authorization_set(values.get("long_shot_authorizations", ""))
    active_cast_authorizations = _authorization_set(values.get("active_cast_authorizations", ""))
    scene_image_authorizations = _authorization_set(values.get("scene_image_authorizations", ""))

    if values.get("artifact") and values["artifact"] != "horizontal-tv-shotlist":
        errors.append("artifact 必须为 horizontal-tv-shotlist")
    if values.get("status") not in {None, "", "draft", "approved"}:
        errors.append("status 只能是 draft 或 approved")
    if values.get("aspect_ratio") and not _horizontal_ratio(values["aspect_ratio"]):
        errors.append("aspect_ratio 必须是宽大于高的横屏比例，例如 16:9")
    if values.get("production_profile") and values["production_profile"] != "ai-video":
        errors.append("production_profile 必须为 ai-video")
    source_value = values.get("source", "").strip()
    if source_value and (Path(source_value).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", source_value)):
        errors.append("source 只能写文件名或项目相对标识，不得泄漏本机绝对路径")

    scene_notes = _scene_notes(text)

    all_tables = find_tables(text)
    shot_tables = [table for table in all_tables if set(HEADERS).intersection(table.headers)]
    if not shot_tables:
        errors.append("未找到七列分镜表")

    main_rows: list[tuple[int, tuple[str, ...]]] = []
    coverage_rows: list[tuple[int, tuple[str, ...]]] = []
    scene_totals: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    main_ids: dict[str, tuple[str, int, int | None]] = {}
    sequence: dict[str, list[tuple[int, int | None, str, int]]] = defaultdict(list)
    coverage_ids: set[str] = set()

    for table in shot_tables:
        if table.headers != HEADERS:
            errors.append(
                f"第 {table.header_line} 行：七列表头必须精确为 " + " | ".join(HEADERS)
            )
            continue
        coverage = is_coverage_table(table)
        for line_no, row in zip(table.row_lines, table.rows):
            if len(row) != len(HEADERS):
                errors.append(f"第 {line_no} 行：必须恰好有七列")
                continue
            if any(not cell.strip() for cell in row):
                errors.append(f"第 {line_no} 行：七列不得留空；无音效写 N/A")
            scene, shot_text, duration_text = row[0].strip(), row[1].strip(), row[2].strip()
            shot = parse_shot_id(shot_text)
            if shot is None:
                errors.append(f"第 {line_no} 行：非法镜号 {shot_text!r}，字母须跳过 I/O")
                continue
            if shot.scene != scene:
                errors.append(
                    f"第 {line_no} 行：镜号 {shot_text!r} 的场次前缀与场次列 {scene!r} 不一致"
                )
            normalized = shot_text.upper()
            try:
                duration = parse_duration(duration_text)
                if duration <= 0:
                    errors.append(f"第 {line_no} 行：时长必须大于 0s")
                if duration > 30:
                    errors.append(f"第 {line_no} 行：镜头 {shot_text} 超过 30s 硬上限")
                elif duration > 20 and normalized not in long_shot_authorizations:
                    errors.append(
                        f"第 {line_no} 行：镜头 {shot_text} 进入 20–30s 区间但缺少镜号级授权"
                    )
                if (duration * 2) != (duration * 2).to_integral():
                    errors.append(f"第 {line_no} 行：时长只允许整数秒或半秒")
            except ValueError as exc:
                errors.append(f"第 {line_no} 行：{exc}")
                duration = Decimal(0)

            camera = row[3].strip()
            picture = row[4]
            sound = row[5].strip()
            edit = row[6].strip()
            if not re.search(r"(?<!\d)(?:1[2-9]|[2-9]\d|1\d{2}|2\d{2})\s*mm(?![A-Za-z])", camera, re.I):
                errors.append(f"第 {line_no} 行：摄影机必须写具体毫米焦段，例如 50mm")
            if re.search(r"全画幅等效|门侧半轴|脏单人|干净单人|脏前景", camera):
                errors.append(f"第 {line_no} 行：摄影机混入禁止的解释性或分类术语")
            if not any(token in camera for token in ("眼平", "胸口", "腰部", "贴地", "肩高", "膝高", "视线高度", "俯拍", "离地")):
                errors.append(f"第 {line_no} 行：摄影机缺少可执行的机位高度")
            if not re.search(r"(?:大全景|全景|中全景|中景|中近景|近景|大近景|极特写|特写)", camera):
                errors.append(f"第 {line_no} 行：摄影机缺少主体与可执行景别")
            if "紧双人" in camera:
                errors.append(f"第 {line_no} 行：双人景别只允许全景、中景、中近景或近景")
            if not any(token in camera for token in ("固定", "手持", "推", "拉", "移", "摇", "跟", "升", "降", "环绕", "变焦", "锁定")):
                errors.append(f"第 {line_no} 行：摄影机缺少固定、手持或具体运动方式")
            if re.search(r"(?:主要功能|第一主体|第二主体|主动人物|轴线|为了保持连续性|意味着.+(?:选择|决定)|AI生成|Seedance|模型限制)", picture, re.I):
                errors.append(f"第 {line_no} 行：画面内容混入分析标签、技术证明或模型说明")
            if re.search(r"连续性\s*[:：]", edit):
                errors.append(f"第 {line_no} 行：剪辑衔接只写切点与衔接方式，不得展示连续性备注")
            if re.search(r"(?:[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z .'-]{0,30})的\s*POV", camera, re.I):
                errors.append(f"第 {line_no} 行：POV 摄影机不得写不可见视点人物名字")
            if re.search(r"POV", camera, re.I) and not re.search(r"POV中景", camera, re.I):
                errors.append(f"第 {line_no} 行：POV 统一只允许写 POV中景")
            if "过肩" in camera and not re.search(r"过.+的?[左右]肩拍", camera):
                errors.append(f"第 {line_no} 行：过肩镜头必须写明过谁的左/右肩拍谁")
            if re.search(r"(?:^|\s)[A-Za-z][A-Za-z .'-]{0,30}:\s*[\"“]", sound):
                errors.append(f"第 {line_no} 行：对白必须放在画面内容，不得放在音效")
            if re.search(r"\b(?:BGM|score|配乐|情绪音乐)\b", sound, re.I):
                errors.append(f"第 {line_no} 行：配乐不得作为普通音效")
            moving_camera = bool(re.search(r"(?:推近|拉远|横移|摇镜|跟随|升起|下降|环绕|变焦|前移|后移)", camera))
            if moving_camera and not re.search(r"(?:镜头|摄影机).*(?:停|落|最后|最终)|(?:停|落幅).*(?:镜头|摄影机)", picture):
                errors.append(f"第 {line_no} 行：运动镜头的画面内容缺少触发、过程或最终停稳状态")

            if coverage:
                coverage_rows.append((line_no, row))
                if shot.coverage is None:
                    errors.append(f"第 {line_no} 行：必要覆盖镜必须使用 -COV<n> 后缀")
                if normalized in coverage_ids:
                    errors.append(f"第 {line_no} 行：覆盖镜号 {normalized} 重复")
                coverage_ids.add(normalized)
                if not re.search(r"(?:保护|替代|备用|接点|风险)", row[6]):
                    errors.append(f"第 {line_no} 行：覆盖镜剪辑衔接必须说明保护的主镜或风险")
            else:
                main_rows.append((line_no, row))
                if shot.coverage is not None:
                    errors.append(f"第 {line_no} 行：COV 镜不得混入成片主序列表")
                if normalized in main_ids:
                    errors.append(f"第 {line_no} 行：主序列镜号 {normalized} 重复")
                main_ids[normalized] = (scene, shot_rank(shot.letters), shot.insertion)
                sequence[scene].append((shot_rank(shot.letters), shot.insertion, normalized, line_no))
                scene_totals[scene] += duration

    for scene, entries in sequence.items():
        notes = scene_notes.get(scene, {})
        scene_image = notes.get("场景图", "")
        if not scene_image:
            errors.append(f"场次 {scene} 缺少场景图")
        elif Path(scene_image).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", scene_image):
            errors.append(f"场次 {scene} 的场景图不得写本机绝对路径")
        elif "待补" in scene_image and scene.upper() not in scene_image_authorizations:
            errors.append(f"场次 {scene} 缺少场景图且没有临时文字草案授权")
        elif "待补" in scene_image and values.get("status") == "approved":
            errors.append(f"场次 {scene} 的场景图仍待补，稿件不得晋升 approved")
        elif "待补" not in scene_image:
            suffix = Path(scene_image).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".tif", ".tiff"}:
                errors.append(f"场次 {scene} 的场景图必须是受支持的图片文件")
            if asset_root is not None and not (asset_root / scene_image).resolve().is_file():
                errors.append(f"场次 {scene} 的场景图文件不存在：{scene_image}")
        if not notes.get("基础全景机位与主轴线"):
            errors.append(f"场次 {scene} 缺少基础全景机位与主轴线")
        if not notes.get("far-side key基准"):
            errors.append(f"场次 {scene} 缺少 far-side key基准")
        prior: tuple[int, int | None] | None = None
        for rank, insertion, shot_id, line_no in entries:
            key = (rank, insertion)
            if prior is not None:
                prior_rank, prior_insert = prior
                if rank < prior_rank or (
                    rank == prior_rank
                    and insertion is None
                    and prior_insert is not None
                ):
                    errors.append(f"第 {line_no} 行：场次 {scene} 镜号顺序倒退到 {shot_id}")
            if insertion is not None:
                base_exists = any(item[0] == rank and item[1] is None for item in entries)
                if not base_exists:
                    errors.append(f"第 {line_no} 行：插镜 {shot_id} 缺少基础镜号")
            prior = key

    for line_no, row in coverage_rows:
        shot = parse_shot_id(row[1])
        assert shot is not None
        base = f"{shot.scene}{shot.letters}"
        if base not in main_ids:
            errors.append(f"第 {line_no} 行：覆盖镜 {row[1]} 的基础主镜 {base} 不存在")

    known_ids = set(main_ids) | coverage_ids
    for authorized in sorted(long_shot_authorizations | active_cast_authorizations):
        if authorized not in known_ids:
            errors.append(f"授权镜号 {authorized} 不存在于当前分镜")
    for authorized_scene in sorted(scene_image_authorizations):
        if authorized_scene not in sequence:
            errors.append(f"场景图临时草案授权场次 {authorized_scene} 不存在于当前分镜")

    declared = _declared_scene_durations(text)
    for scene, actual in scene_totals.items():
        if scene not in declared:
            errors.append(f"场次 {scene} 缺少场次时长")
        elif declared[scene] != actual:
            errors.append(
                f"场次 {scene} 时长应为 {format_decimal(actual)}s，声明为 {format_decimal(declared[scene])}s"
            )

    total = sum(scene_totals.values(), Decimal(0))
    if values.get("total_duration"):
        try:
            declared_total = parse_duration(values["total_duration"])
            if declared_total != total:
                errors.append(
                    f"total_duration 应为 {format_decimal(total)}s，声明为 {format_decimal(declared_total)}s"
                )
        except ValueError as exc:
            errors.append(f"frontmatter total_duration：{exc}")

    try:
        dialogue = _load_dialogue_manifest(dialogue_manifest)
    except (ValueError, json.JSONDecodeError) as exc:
        errors.append(f"对白清单错误：{exc}")
        dialogue = []
    picture_text = "\n".join(row[4] for _, row in main_rows)
    for line in dialogue:
        count = picture_text.count(line)
        if count == 0:
            errors.append(f"权威对白遗漏：{line}")
        elif count > 1:
            errors.append(f"权威对白重复 {count} 次：{line}")

    summary: dict[str, object] = {
        "main_shots": len(main_rows),
        "coverage_shots": len(coverage_rows),
        "total_duration": f"{format_decimal(total)}s",
        "scenes": {scene: f"{format_decimal(value)}s" for scene, value in scene_totals.items()},
    }
    return errors, summary


def validate_file(path: Path, *, dialogue_manifest: Path | None = None) -> list[str]:
    errors, _summary = validate_shotlist_text(
        read_text(path), dialogue_manifest=dialogue_manifest, asset_root=path.parent
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--dialogue-manifest", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    text = read_text(args.path)
    errors, summary = validate_shotlist_text(
        text, dialogue_manifest=args.dialogue_manifest, asset_root=args.path.parent
    )
    source_value = parse_frontmatter(text).values.get("source", "").strip()
    if source_value:
        source = Path(source_value)
        if not source.is_absolute():
            source = (args.source_root or args.path.parent) / source
        if not source.resolve().is_file():
            errors.append(f"权威源文件无法解析：{source_value}")
    if args.as_json:
        print(json.dumps({"valid": not errors, "errors": errors, "summary": summary}, ensure_ascii=False, indent=2))
    elif errors:
        for error in errors:
            print(f"ERROR: {error}")
    else:
        print(f"OK: {args.path} ({summary['main_shots']} 主镜, {summary['coverage_shots']} 覆盖, {summary['total_duration']})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
