# Seedance Stage 3 模型适配

本文件只描述适配原则，不把可能变化的产品能力写成永久导演规则，也不得覆盖或改写`seedance-prompt-rules-master.md`中的任何核心规则。

## 配置门禁

开始 Stage 3 前确认用户实际使用的 Seedance 版本、产品入口、参考资产数量、多镜能力、音频能力、续接方式和提示词语言。当前工作流的片段硬上限固定为`30s`，`27–30s`为优先目标区间；能力信息可能变化时以用户明确的新项目规则为准，不根据家族名称猜测。

## 适配原则

- 每个 Stage 1 镜头必须完整落在一个 Stage 3 片段内；不得以模型适配为由拆分导演镜头。
- 单次时长与多镜能力只决定片段边界，不改变权威成片。
- 参考资产控制静态外观，提示词保留动态状态与当前动作。
- 每个独立调用不假设看见上一段提示词或视频；只有当前入口已经核实支持时才使用续接、首尾帧或视频参考。
- 生成余量用于剪辑恢复权威切点，不改变权威镜头时长。
- 字幕、标题、聊天 UI 和精确字形交给后期，不要求视频模型可靠绘制。

## 配置记录

Stage 3 成稿 frontmatter 记录：

```yaml
model: seedance
model_version: user-confirmed-version
surface: user-confirmed-surface
profile: horizontal-series
verified_on: YYYY-MM-DD
source: approved-shotlist.md
```

任何能力升级只创建新的模型配置或提示词版本，不回写 approved 分镜。
