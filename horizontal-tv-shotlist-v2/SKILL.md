---
name: horizontal-tv-shotlist-v2
description: 将完整一集或指定场次剧本设计为北美流媒体单机位横屏电视剧 AI 视频分镜，并按“Stage 1 七列导演分镜 → Stage 2 场次固定类别 → Stage 3 Seedance 2.5 五列 approved 提示词”交付。用于 16:9 或用户指定横屏比例的对白、关系、悬疑、动作、群戏、历史战争背景与医疗正剧；也用于场景图空间分析、Blocking、轴线与光影连续性、反应覆盖、平台敏感词预检和 EP4/EP6 类问题回归。不得用于竖屏短剧、多机位棚拍喜剧或未经批准跨阶段生成。
---

# 横屏剧集分镜导演 V2

作为独立 V2 工作，不读取或依赖 V1、竖屏技能或单项目文件。项目人物、场景、服装、道具、伤处、轴线和光源位置只来自本次输入，不写回技能。

## 权威顺序

冲突时依次服从：用户当前明确修改 → 受保护剧本事实与对白 → 已批准上游阶段 → 当前项目配置与参考图 → 题材模块 → 全题材通用规则。平台措辞调整不得改变剧本事实、事件顺序、动作强度或受保护对白。

## 必须加载的文件

- 开始任何项目时，完整读取[项目输入 Schema](references/project-input-schema.md)。
- 执行 Stage 1 时，完整读取[Stage 1 导演分镜规则](references/stage1-directing-rules.md)；创作完成后读取[连续性检查表](references/continuity-checklist.md)。
- 战争历史背景、医疗、伤员、撤离或历史低照度场景，额外完整读取[战争历史背景＋医疗正剧模块](references/war-medical-drama-profile.md)。该模块不得覆盖通用硬规则。
- 执行 Stage 2 时，完整读取[Stage 2 固定提示词规则](references/stage2-fixed-prompt-rules.md)。
- 执行 Stage 3 时，完整读取[Stage 3 Seedance 2.5 规则](references/stage3-seedance-rules.md)和[平台安全语言](references/platform-safety-language.md)，并再次读取连续性检查表的 Stage 3 部分。
- 修改技能或回归时，读取[回归案例](references/regression-cases.md)，运行`scripts/validate_prompts.py --self-test`。

## 固定工作顺序

**剧本事实核对 → 戏剧 Beat → 空间与光源图 → Blocking 与轴线 → 主剪辑镜头 → 备用覆盖镜头 → 景别升级曲线 → 运镜层级 → 连续性复核 → Stage 2 固定类别 → Stage 3 逐镜编译 → Checklist 与敏感词复核。**

不得因 Seedance 限制反向改写 Stage 1。发现上游问题时退回对应阶段，不在后续阶段静默修复。

## 阶段门禁

### Stage 1

有整集剧本时先读完整一集，再交付指定范围。结合场景图和项目配置制作七列分镜：`场次｜镜号｜时长｜摄影机｜画面内容｜音效｜剪辑衔接`。新稿和修订稿均为`draft`；用户按场次整体确认后才标记`approved`。

### Stage 2

只有当前场次 Stage 1 为`approved`才启动。只建立会跨多个镜头稳定复用、容易漂移且实质影响生成的最少类别。Stage 2 先输出`draft`卡片；用户按场次整体确认后标记`approved`。任一必需类别未确认时不得进入 Stage 3。

### Stage 3

只有当前范围的 Stage 1 与 Stage 2 均为`approved`才启动。生成前先执行敏感词、参考图、轴线、肩侧、动作状态和固定类别映射预检；存在未解决问题时只输出“Stage 3 生成前阻塞清单”，不得生成提示词稿。

预检通过后直接生成完整展开的 Stage 3 `approved`，不生成 Stage 3 draft，也不设置二次批准门禁。输出固定为`镜号｜片段｜时长｜分镜内容｜Seedance提示词`；确有问题或已接受风险时才增加`备注`列。最终提示词直接展开为自然的`声音、光影、群演、环境`等类别，不出现`固定类别：`、类别占位符或类别名称。

## 状态与修订

保持无关镜号、文字、类别、片段和批准状态稳定。用户对单个项目的修改只作用于该项目；只有用户明确要求优化技能时，才迁移为通用规则或题材模块。跨集状态只从用户明确批准的分镜更新。

## 验证

完成 V2 文件修改后依次运行：

```bash
python3 /Users/wuxiaoyu/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-dir>
python3 scripts/validate_prompts.py --skill-root .
python3 scripts/validate_prompts.py --self-test
```

结构脚本只证明格式和显式约束通过，不能替代导演质量判断。代表性结果仍需用户批准。
