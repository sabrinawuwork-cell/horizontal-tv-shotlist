# Seedance Stage 3 模型路由

本文件只负责模型路由和共同片段边界，不覆盖或改写`seedance-prompt-rules-master.md`中的核心正文。

## 当前默认适配器

横屏 Agent 当前 Stage 3 默认使用`seedance-2.5`。生成前完整读取[Seedance 2.5 专属适配](model-seedance-2.5.md)。自然动作/表情写法、独立`情感解析`块、主体预算、参考图视图选择及通用条件式`备注`列只属于 Seedance 2.5；不得自动用于其他 Seedance 版本或其他视频模型。

如果用户指定其他模型或版本，必须另建独立适配文件并确认输出差异；不得把 Seedance 2.5 的智能程度、情感解析格式或自然语言动作策略当作家族通用能力。

## 共同片段边界

- 每个 Stage 1 镜头必须完整落在一个 Stage 3 片段内，不得以模型适配为由拆分导演镜头。
- 每个片段硬上限为`30s`，`27–30s`为优先目标区间，不是最低时长；具体模型的主体稳定性可以要求更短片段。
- 单次时长与多镜能力只决定片段边界，不改变 Stage 1 权威内容。
- 每个独立生成片段都不假设看见上一片段提示词；片段首镜必须自包含重建可见状态。
- 参考资产控制静态外观，提示词保留动态状态与当前动作。

Stage 3 frontmatter 至少记录：

```yaml
model: seedance-2.5
surface: user-confirmed-surface
profile: horizontal-series
verified_on: YYYY-MM-DD
source: approved-shotlist.md
```

任何能力升级只创建新的模型配置或提示词版本，不回写 approved 分镜。
