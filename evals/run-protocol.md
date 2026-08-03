# Horizontal Evaluation Run Protocol

## 固定条件

- 为基线与实验版使用相同模型、推理强度、原始剧本、参考资产、拍摄语言、画幅和用户提示。
- 每个版本在独立新上下文中运行两次，不允许候选看见其他输出，不从两次中择优。
- 开发集可以诊断和修改；候选冻结后才启封留出集。私有源、输出和标签不得进入`dist`或安装包。

## Stage 1 提示

```text
Use $horizontal-tv-shotlist from the supplied skill folder. Read the complete attached episode and the supplied scene images, then create the requested Chinese seven-column director shotlist for a North American single-camera horizontal streaming series. Establish the scene map, base camera/axis and far-side key from the images. Preserve every source line and event. Keep exact dialogue inside 画面内容. Use only 场次 | 镜号 | 时长 | 摄影机 | 画面内容 | 音效 | 剪辑衔接. In 摄影机, write 主体＋景别 → numeric focal length such as 50mm → camera position relative to the visible subject → height and angle → fixed/handheld/movement. Do not write full-frame-equivalent, dirty single, clean single, dirty foreground or half-axis labels. For an over-the-shoulder shot, name whose left/right shoulder is crossed and who is photographed; for any other blurred foreground, name the visible person or object directly. Keep the camera description concise and place movement timing, trigger, path and final stop in the natural-language 画面内容 paragraph. In intimacy, ambiguity, emotional push-pull and two/three-character relationship scenes, do not let repeated two-shots replace key single shots, affected-character reactions or motivated details. Keep most shots at 5–10 seconds; use 10–20 seconds sparingly. Do not create 20–30 second shots or shots with more than four active characters unless the user authorizes the exact shot ID. Never exceed 30 seconds. Deliver Stage 1 only.
```

## Stage 2 提示

```text
Use $horizontal-tv-shotlist from the supplied skill folder. Treat the attached Stage 1 approved shotlist and reference images as authority. For each scene, draft concise Chinese fixed-category cards for every speaking character's required base voice; add scene/phase voice state only when it remains stable across a continuous phase. Define lighting by character/camera/orientation relationship and background by camera direction. Keep them separate by default, but use one fixed-visual card when lighting, background, environment or extras are physically inseparable. Infer stable lux/K ranges from the scene image when appropriate; for dynamic flashing or continuously changing sources, prioritize direction, contrast, color relation and exposure result. Add only factually required environment, extras/onlookers and scene sound. Do not list applicable shot IDs, do not include 无配乐无字幕, generic skin-quality tails, or temporary red/blue-marker restrictions. Deliver Stage 2 draft only and wait for scene-level approval.
```

## Stage 3 提示

```text
Use $horizontal-tv-shotlist from the supplied skill folder. Compile only the approved Stage 1 main sequence using the approved Stage 2 fixed categories. Plan globally numbered Seedance segments with a 30-second hard limit and a 27–30-second preferred target. Every Stage 1 shot must remain whole inside one segment. Output 场次 | 片段 | 镜号 | 时长 | Seedance提示词. In draft, show only concise fixed-category names for user classification review. Add the generic real-skin phrase only when skin is visible in that shot, and add temporary negative restrictions only when the user explicitly requests them for Stage 3. After approval, mechanically replace fixed-category names with the exact Stage 2 approved card text, change nothing else, and keep exactly one 无配乐无字幕 at the final end of every shot prompt.
```

## 发布门禁

先运行结构、映射、项目状态与 Excel 检查，再使用`rubric.md`盲评全部 10 类以上场景和完整一集。所有硬错误清零、综合至少 85、三项创作门禁至少 8/10，并取得用户对代表性结果的批准后，才构建并安装发行包。
