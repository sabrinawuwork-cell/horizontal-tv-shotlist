# Stage 2 极简固定提示词输出 Schema

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
speaking_characters: Damon, Teresa
environment_required: no
extras_required: no
scene_sound_required: no
---
```

正文不用表格，固定采用“一个三级标题对应一张类别卡片”。允许的类别前缀仅为：`基础音色·`、`场次音色·`、`阶段音色·`、`光影·`、`背景·`、`环境·`、`群演·`、`场次声音·`。`固定视觉·`已废止并视为错误。

若必要性测试判定整场不需要任何固定类别，正文可以没有卡片；保留完整 frontmatter，并在标题下写一段非卡片说明“本场无须建立固定类别”。该说明不会进入 Stage 3。

```markdown
### 基础音色·Damon

音色：……

### 光影·Damon

光影：主光源……；人物亮面……lux，暗面……lux；人物背后为……，背景……lux；整体……

### 光影·Teresa

光影：……

### 光影·Damon与Teresa

光影：……
```

每名实际说话角色必须有`基础音色·角色名`。已在项目中批准的基础音色仍需完整展示并逐字复用；`场次音色·`和`阶段音色·`可选。

每场不强制建立光影或独立背景卡；只要 Stage 1 中存在反复出现的主要人物受光，就应为实际需要的单人/多人关系建立最少光影卡。普通背景写进光影卡；独立`背景·`仅在不同背景方向反复出现、影响空间识别且参考图无法稳定控制时建立。

`environment_required`、`extras_required`和`scene_sound_required`必须明确写`yes/no`。`yes`必须有对应卡片，`no`不得出现对应卡片。普通室内、无持续群演、无清楚可闻的持续声音时均写`no`。

正文机械展开前把卡片内段落换行转换为`<br>`。卡片不得包含`无配乐无字幕`、通用皮肤质感句、镜号、范围、适用镜号、待替换占位符、“同上”“保持不变”或临时负面限制。approved 稿不得存在待确认项。
