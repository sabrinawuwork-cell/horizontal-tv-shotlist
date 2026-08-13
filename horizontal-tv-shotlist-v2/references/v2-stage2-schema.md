# V2 Stage 2 极简固定提示词输出 Schema

本文件从 V1 Schema 原样派生，只改变用户明确批准的“音色默认可不建立”和新增群演分类；其他卡片排版、frontmatter、状态和批准规则保持不变。

Stage 2 使用 Markdown 权威稿，`artifact`固定为`horizontal-tv-stage2`，每个场次独立标记状态。类别名称在当前场次内唯一，不列镜号、镜号范围或适用镜号。

```yaml
---
artifact: horizontal-tv-stage2
project: project-id
episode: E01
version: v001
status: draft
source_stage1: episode-E01-approved.md
scene: "1"
scene_image: assets/scene-1.png
stage1_sha256: <digest>
speaking_characters: 角色A, 角色B
voice_prompt_required: no
environment_required: no
extras_required: no
scene_sound_required: no
---
```

正文不用表格，固定采用“一个三级标题对应一张类别卡片”。允许的类别前缀仅为：`基础音色·`、`场次音色·`、`阶段音色·`、`光影·`、`背景·`、`环境·`、`群演外观·`、`群演动作·`、`群演光影·`、`场次声音·`。`固定视觉·`已废止并视为错误。

若必要性测试判定整场不需要任何固定类别，正文可以没有卡片；保留完整 frontmatter，并在标题下写一段非卡片说明“本场无须建立固定类别”。该说明不会进入 Stage 3。

```markdown
### 基础音色·角色A

音色：……

### 光影·角色A

光影：主光源……；人物亮面……lux，暗面……lux；人物背后为……，背景……lux；整体……

### 光影·角色B

光影：……

### 光影·角色A与角色B

光影：……
```

基础音色默认不创建。只有用户在当前任务中明确要求音色提示词，才将`voice_prompt_required`设为`yes`，并为实际说话角色建立`基础音色·角色名`。历史项目卡片、旧配置或用户未在当前任务明确启用的音色参考，均不构成授权。`场次音色·`和`阶段音色·`始终为可选类别。

室外场次必须执行持续环境变化检查。风、水流、云层、雾气、草叶、衣角、烟雾或其他变化覆盖多镜并影响连续性时，`environment_required`必须为`yes`并有对应环境卡；没有持续变化时写`no`，不机械建卡。

每场不强制建立光影或独立背景卡；只要 Stage 1 中存在反复出现的主要人物受光，就应为实际需要的单人/多人关系建立最少光影卡。普通背景写进光影卡；独立`背景·`仅在不同背景方向反复出现、影响空间识别且参考图无法稳定控制时建立。

`environment_required`、`extras_required`和`scene_sound_required`必须明确写`yes/no`。`yes`必须有对应卡片，`no`不得出现对应卡片。普通室内、无持续群演、无清楚可闻的持续声音时均写`no`。

正文机械展开前把卡片内段落换行转换为`<br>`。卡片不得包含`无配乐无字幕`、通用皮肤质感句、镜号、范围、适用镜号、待替换占位符、“同上”“保持不变”或临时负面限制。approved 稿不得存在待确认项。
