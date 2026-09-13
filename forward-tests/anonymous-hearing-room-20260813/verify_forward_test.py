#!/usr/bin/env python3
"""Check source-to-output relationships; visual judgments live in continuity-notes.md."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def metadata(text: str) -> dict[str, str]:
    block = text.split("---", 2)[1]
    return {k.strip(): v.strip().strip('"') for k, v in
            (line.split(":", 1) for line in block.splitlines() if ":" in line)}


def require(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"CHECK: {name}")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_rows(text: str, id_column: int) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) > id_column and re.fullmatch(r"1[A-Z]+(?:-COV\d+)?", cells[id_column]):
            rows.append(cells)
    return rows


def seconds(value: str) -> Decimal:
    return Decimal(value.removesuffix("s"))


def quotes(text: str) -> list[str]:
    return re.findall(r"：\s*“([^”]+)”", text)


s1, s2, s3 = (read(f"stage{n}-approved.md") for n in (1, 2, 3))
m1, m2, m3 = map(metadata, (s1, s2, s3))
all_s1 = table_rows(s1, 1)
main = [row for row in all_s1 if "-COV" not in row[1]]
coverage = [row for row in all_s1 if "-COV" in row[1]]
compiled = table_rows(s3, 0)
require("Stage 1 七列、Stage 3 五列", all(len(r) == 7 for r in all_s1) and all(len(r) == 5 for r in compiled))
ids = [row[1] for row in main]
require("Stage 3 主镜完整、唯一、按 Stage 1 顺序输出", ids == [r[0] for r in compiled] and len(set(ids)) == len(ids))
require("覆盖独立于主序列且归属镜真实存在", all(r[1].split("-COV")[0] in ids for r in coverage))
require("主镜与覆盖时长均处于有效范围", all(Decimal(0) < seconds(r[2]) <= 30 for r in all_s1))
require("Stage 1 主序列累计时长等于声明，覆盖不重复计入", sum(seconds(r[2]) for r in main) == seconds(m1["total_duration"]))

# Read authority locations from metadata instead of asserting the presence of fixed words.
source_path = ROOT / m1["source"]
manifest_path = ROOT / m1["dialogue_manifest"]
notes_path = ROOT / m1["continuity_notes"]
require("当前源剧本、对白清单和复核记录摘要匹配", all(
    path.is_file() and digest(path) == m1[key]
    for path, key in [(source_path, "source_sha256"), (manifest_path, "dialogue_manifest_sha256"), (notes_path, "continuity_notes_sha256")]
))
image_paths = dict(re.findall(r"## 场次 (\d+)\n\n- 场景图：([^\n]+)", s1))
image_hashes = json.loads(m1["scene_images_sha256"])
require("场景图按场次绑定当前原始文件", set(image_paths) == set(image_hashes) == {r[0] for r in main} and all((ROOT / name).is_file() and digest(ROOT / name) == image_hashes[scene] for scene, name in image_paths.items()))
source_dialogue = quotes(source_path.read_text(encoding="utf-8"))
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["dialogue"]
require("对白清单与源剧本按顺序逐字一致", manifest == source_dialogue)
require("Stage 1 主序列对白与源剧本按顺序逐字一致", [q for r in main for q in quotes(r[4])] == source_dialogue)
main_by_id = {r[1]: r for r in main}
require("有对白的完整备用覆盖复用其主镜对白", all(not quotes(r[4]) or quotes(r[4]) == quotes(main_by_id[r[1].split("-COV")[0]][4]) for r in coverage))
require("Stage 2/3 绑定当前 Stage 1 与 Stage 2", all([
    (ROOT / m2["source_stage1"]).resolve() == (ROOT / "stage1-approved.md").resolve(),
    (ROOT / m3["source_stage1"]).resolve() == (ROOT / "stage1-approved.md").resolve(),
    (ROOT / m3["source_stage2"]).resolve() == (ROOT / "stage2-approved.md").resolve(),
    m2["stage1_sha256"] == m3["stage1_sha256"] == digest(ROOT / "stage1-approved.md"),
    m3["stage2_sha256"] == digest(ROOT / "stage2-approved.md"),
]))

elapsed: dict[str, Decimal] = {}
for upstream, downstream in zip(main, compiled):
    shot, segment, duration, merged, prompt = downstream
    require(f"{shot} 时长及三列追溯与上游相同", duration == upstream[2] and merged == "<br><br>".join(
        label + upstream[i] for label, i in [("摄影机：", 3), ("画面内容：", 4), ("音效：", 5)]
    ))
    time_range = re.match(r"([\d.]+)-([\d.]+)秒。", prompt)
    start = elapsed.get(segment, Decimal(0))
    end = start + seconds(duration)
    require(f"{shot} 片段内累计区间与实际行时长相同", time_range is not None and (Decimal(time_range[1]), Decimal(time_range[2])) == (start, end))
    elapsed[segment] = end
    action = next(block for block in prompt.split("<br><br>") if block.startswith("动作/表情："))
    require(f"{shot} 提示词对白与 Stage 1 当镜逐字一致", quotes(action) == quotes(upstream[4]))
require("各片段都不超过 30 秒", all(0 < value <= 30 for value in elapsed.values()))
require("片段累计总长与主序列相同", sum(elapsed.values()) == seconds(m1["total_duration"]))

cards = {name: body.strip() for name, body in re.findall(r"^### ([^\n]+)\n\n(.*?)(?=\n### |\Z)", s2, re.M | re.S)}
binding_path = ROOT / m3["stage3_bindings"]
bindings = json.loads(binding_path.read_text(encoding="utf-8"))
require("编译记录绑定当前文件与完整主镜", digest(binding_path) == m3["stage3_bindings_sha256"] and bindings["source_stage1_sha256"] == digest(ROOT / "stage1-approved.md") and set(bindings["shots"]) == set(ids))
for upstream, downstream in zip(main, compiled):
    shot, _, _, _, prompt = downstream
    record = bindings["shots"][shot]
    selected = record["fixed_cards"]
    blocks = prompt.split("<br><br>")
    require(f"{shot} 选定固定卡均以当前批准正文完整展开", len(selected) == len(set(selected)) and all(name in cards and cards[name] in blocks for name in selected))
    expected_blocks = [cards[name] for name in selected] + [e["text"] for e in record.get("exceptions", {}).values()]
    actual_blocks = [block for block in blocks if re.match(r"^(?:光影|背景|环境|群演|外观)：", block)]
    require(f"{shot} 实际固定块没有缺失、添加或改写", Counter(actual_blocks) == Counter(expected_blocks))
    for kind, exception in record.get("exceptions", {}).items():
        require(f"{shot} {kind} 单镜例外有上游动作证据且逐字展开", exception["text"] in blocks and bool(exception["basis"].strip()) and bool(exception["stage1_evidence"].strip()) and exception["stage1_evidence"] in upstream[4])
require("音色关闭时输出不包含音色参考块", m2["voice_prompt_required"] == "no" and "音色参考" not in s3)
print(f"已核对 {len(main)} 个主镜、{len(coverage)} 个覆盖、{len(elapsed)} 个片段的来源关系。")
print("这些检查不判断空间投影、动作可见性、实际语速、光照或生成质量；详见 continuity-notes.md。")
