#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
stage1 = (ROOT / "stage1-approved.md").read_text(encoding="utf-8")
stage2 = (ROOT / "stage2-approved.md").read_text(encoding="utf-8")
stage3 = (ROOT / "stage3-approved.md").read_text(encoding="utf-8")


def require(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"PASS: {name}")


def stage1_row(shot: str) -> str:
    match = re.search(rf"(?m)^\| 1 \| {re.escape(shot)} \|.*$", stage1)
    if not match:
        raise AssertionError(f"找不到Stage 1镜号{shot}")
    return match.group(0)


def stage3_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for line in stage3.splitlines():
        if re.match(r"^\| 1[A-Z]+ \| 片段\d+ \|", line):
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


require(
    "时长从内容反推并覆盖快反应、普通对白、重要表演与复杂调度尺度",
    all(token in stage1 for token in ("| 1C | 3s |", "| 1D | 2s |", "| 1B | 5s |", "| 1J | 8s |", "| 1F | 10s |")),
)
require("所有主镜不超过30秒", not re.search(r"\| 1[A-Z]+ \| (?:3[1-9]|[4-9]\d)s \|", stage1))

first = stage1_row("1A")
require(
    "第一镜建立时间、人物关系、群体、出入口和核心光源",
    all(token in first for token in ("傍晚", "角色甲", "角色乙", "角色丙", "四名居民", "主入口", "应急出口", "公告板", "线性灯")),
)

move = stage1_row("1F")
post_move = "\n".join(stage1_row(shot) for shot in ("1G", "1H", "1J", "1K", "1L"))
require(
    "换位镜完整记录起点、路线、摄影机跨越过程、停稳节点和最终站位",
    all(token in move for token in ("长桌西端", "绕过北端", "向右弧形横移", "房间西侧", "停稳", "公告板前")),
)
require("换位后后续机位改用西侧或新人物相对机位", "房间西侧" in post_move and "角色甲左前方" in post_move)
require(
    "换位后光影由角色甲自身左侧亮面重算为自身右侧亮面",
    "光影·角色甲·桌西" in stage2
    and "角色甲自身左侧脸颊" in stage2
    and "光影·角色甲·公告板站位" in stage2
    and "角色甲自身右侧脸颊边缘" in stage2,
)

reveal = [stage1.find(token) for token in ("空的透明文件套", "露出一角折叠纸页", "露出清晰的正式印章", "把脚移开")]
require("信息揭示顺序为搜索失败→异常线索→决定性细节→人物反应", all(index >= 0 for index in reveal) and reveal == sorted(reveal))

require(
    "长演讲由说话者发展、听者主序列反应和情绪峰值共同推进",
    all(shot in stage1 for shot in ("| 1G |", "| 1H |", "| 1J |"))
    and "角色乙与角色丙双人中近景" in stage1
    and "角色甲的近景，85mm" in stage1,
)
require(
    "长演讲备用覆盖与成片主序列分离",
    all(token in stage1 for token in ("1G-COV1", "1H-COV1", "1J-COV1", "备用覆盖", "备用反应")),
)

rows = stage3_rows()
segment_totals: dict[str, float] = {}
for row in rows:
    segment_totals[row[1]] = segment_totals.get(row[1], 0.0) + float(row[2].removesuffix("s"))
require("Stage 3片段时长分别为22秒、28秒和18秒且均不超过30秒", segment_totals == {"片段1": 22.0, "片段2": 28.0, "片段3": 18.0})
require("Stage 2默认关闭音色", "voice_prompt_required: no" in stage2)
require("Stage 3默认不存在音色参考块", "音色参考" not in stage3 and "【角色名音色参考：】" not in stage3)
require("Stage 3保持五列且没有无问题备注列", "| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 |" in stage3 and "| 备注 |" not in stage3)

print("PASS: 匿名横屏正剧前向测试全部专项断言通过")
