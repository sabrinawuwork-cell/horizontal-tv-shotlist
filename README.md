# 横屏剧集分镜导演 V2

本仓库只维护 [horizontal-tv-shotlist-v2](horizontal-tv-shotlist-v2/SKILL.md)。使用、修改和安装均以该目录为入口；根目录旧版已删除。

PR提交范围已于2026-09-14整理：运行包、正式测试及fixtures完整保留，大型实验过程材料留在本机、不进入main。需要追溯时见[验证材料索引](docs/验证材料索引.md)与[整理核对](docs/PR整理核对.md)。

当前阶段已结项（2026-09-13）：**Stage 1导演与分镜、Stage 2固定条件、Stage 3提示词与离线交付，以及本阶段已确认的检查器问题均已收束，继续采用当前V2。**

- Stage 3已落实必要制作条件承接、真实参考用途、人物首次入画定义，以及主稿/task/request同步；没有新增固定卡类型、检查器或审批层。
- 两个Astra / medium干净上下文分别用最初V2和当前冻结版，完成同输入双场的三阶段、两本工作簿与实际任务交接。当前版11镜82秒，原始版15镜86秒；提示词交接有明确改善，导演组织在本例小幅偏向当前，不能由单次对照证明所有题材全面提升或重复运行稳定性。
- 既有209项全套、后续30项引用检查及本轮82项相关验证分别保留其版本与范围；母版核心及兼容检查通过。测试通过不代表语义或视频效果自动通过。
- 官方文档与社区经验核对后，一句话总述、进一步集中重复限制只作为可选写法，不增加强制规则，也不追加为结项待办。

提交沿用[PR #12](https://github.com/sabrinawuwork-cell/horizontal-tv-shotlist/pull/12)，不自动合并main或关闭整个Issue #11。真实媒体生成及成片执行、跨运行稳定性与总体返工效率仍未验证，属于后续验证范围，不阻塞本阶段结项。

当前依据：[决策与变更](docs/决策与变更.md)、[完整双版本对照](forward-tests/full-ab-20260913/README.md)、[Stage3定向修复与验收](forward-tests/stage3-handoff-20260913/README.md)。此前分批处置及[历史验收包（本机归档）](docs/验证材料索引.md#issue11-whole-method-20260909 "原路径：forward-tests/issue11-whole-method-20260909/version-closeout/README.md")保留追溯，唯一运行入口仍为`horizontal-tv-shotlist-v2/`。

当前 V2 来自 `a7968ef`（PR #10：Update V2 and add anonymous forward test）。V2 内继承的格式规则、示例和兼容测试属于当前包的组成部分，不需要单独的 V1 项目。

当前导演工作以整场方案为先：给定剧本和场景，先保存包含连续演出、观看和连接决定的 `director-plan.md`，再据此生成七列分镜。必要信息与反应组成主序列，备用覆盖随后按需选择；Stage 2/3 忠实落实已确定的决定。方案是 Stage 1 内部工作，不增加用户审批轮，不能在分镜完成后倒写解释。

- 技能入口：[V2 SKILL.md](horizontal-tv-shotlist-v2/SKILL.md)
- 匿名案例：[社区听证室](forward-tests/anonymous-hearing-room-20260813/forward-test-report.md)
- 决策与验证记录：[决策与变更](docs/决策与变更.md)
- 先方案后拆镜的实际验证：[分阶段方案与分镜](forward-tests/scene-first-hearing-room-20260908/report.md)
- 原版与修改版的八稿重复盲评：[分镜质量与稳定性对照](forward-tests/stage1-ab-20260908/report.md)（导演组织初步偏向修改版，稳定性未分出高下）

## 创作交付与维护检查

创作时使用当前阶段入口：Stage1检查当前分镜与来源，Stage2检查固定条件与批准来源；交付完整Stage3任务时直接导出，该操作已包含Stage3来源/映射检查，不用先把全部校验脚本跑一遍。仅交五列表时才单独运行Stage3检查。原表更新有自身的范围预览和写入保护，继承格式工具由这些入口复用。

导演设计、分镜和提示词的内容质量现在就依据完整剧本、导演意图、连续性和制作条件判断；分别说明原有能力、已确认bug、需求兑现和返工变化。工程检查不能替代内容验收，未生成视频也不能推迟这些判断。视频另行验证提示词与模型配合后的执行效果。

以下是技能代码/规则变更或安装更新后的维护命令，按受影响范围使用，不是每次创作的必经流程：

```sh
python3 -m unittest discover -s horizontal-tv-shotlist-v2/tests
python3 horizontal-tv-shotlist-v2/scripts/validate_v1_compatibility.py
python3 horizontal-tv-shotlist-v2/scripts/validate_seedance_rules_master.py horizontal-tv-shotlist-v2/references/v2-seedance-prompt-rules-master.md
python3 horizontal-tv-shotlist-v2/scripts/validate_v2_prompts.py --skill-root horizontal-tv-shotlist-v2 --self-test
```

V2 三阶段入口还校验真实来源、当前摘要、镜头与对白完整性、片段时间和固定卡绑定。项目内的连续性记录负责保留实际判断依据；字符串与摘要检查不替代说话人、空间、表演复核，也不等于实际视频生成验收。具体内部记录见 [V2 权威输入与连续性](horizontal-tv-shotlist-v2/references/v2-authority-and-continuity.md)。

新生成的 Stage 1 使用 `validate_v2_shotlist.py <Stage1.md> --require-plan` 检查当前方案绑定；无方案的外部已批准分镜可只验证交付关系，不倒造其创作过程。听证室旧修订案例保留为历史交付回归，需要时使用其`verify_forward_test.py`，不能用它的通过声称验证了先方案后拆镜的方法。兼容检查同时核对历史文件与明确批准的方法修订；Seedance核心母版仍保持原指纹。

## 构建安装包

```sh
python3 horizontal-tv-shotlist-v2/scripts/build_distribution.py --output dist/horizontal-tv-shotlist-v2
```

用户已确认“情感解析”保留现行可选机制，评测与运行语义一致；不要求逐镜必填，也不全部删除。
