# Clean sub-agent 全流程实跑：Astra / low

后续状态：本轮冻结原稿和失败日志未改。用户随后授权图片引用检查瘦身，原Stage3未改字即成功导出，见[后续工具修复](../reference-check-thin-20260913/README.md)。下方未解决状态描述的是本轮冻结实跑时点，不代表该工具问题仍未处理；Stage3内容问题仍待下一轮。

冻结版本32b4fb6。按用户最新要求完整实际运行Stage1、Stage2、Stage3，Stage3问题只记录不修；不访问旧答案、不调用媒体生成或上传。本轮批准仅为离线模拟。

**结论：Stage1、Stage2可以按本轮范围收束；检查器/任务导出仍有一处真实解析问题，不能将Stage3以外所有环节统称全部通过。**

- Stage1从原始输入独立生成导演方案和6镜42秒分镜，一轮字段/场景说明格式自修后通过；首次稿和修后稿均留存。主代理对源阅读确认对白、持盒者、完整松手后离开、门内叫停、推盒不接、收手未出口、退让后转身等待保留；没有用固定镜数或旧答案验收。具体体态、动态遮挡与成像未验证，不把文字声称可见当实拍证明。
- Stage2一段共同日光条件，首稿检查通过；北窗/南门及熄灭油灯固定，人物转身受光由逐镜决定，不锁死全场明暗侧。
- Stage3首稿保存并实际调用任务导出，因参考标签被误解析退出1。没有生成可提交任务正文；两本xlsx已生成，Stage3工作簿只承载失败首稿，不能据其存在认定交付通过。首稿未修改，提示词问题留待下一轮。
- 209项单元测试一次全过；兼容35份基线、母版完整性、最近两项补丁的本地最小核对通过。单元测试无skip不代表所有语义评测都自动完成；日志中的UNASSESSED内容审读项未算自动内容通过。
- 未解决工具问题：计划正确声明`@Image 1`、`@Image 2`，正文`@Image 1与@Image 2`被正则的Unicode词边界截成`@Image`。现行文档未规定编号标签后必须空格；这是解析误判，不应全部归咎Stage3创作并排除。尚未改源码或用空格修稿绕过。

证据：[实跑报告（本机归档）](../../docs/验证材料索引.md#clean-sub-full-astra-low-20260913 "原路径：output/run-report.md")、[命令与退出码（本机归档）](../../docs/验证材料索引.md#clean-sub-full-astra-low-20260913 "原路径：logs/results.json")、[主代理核对（本机归档）](../../docs/验证材料索引.md#clean-sub-full-astra-low-20260913 "原路径：host-verification.json")、[Stage1分镜（本机归档）](../../docs/验证材料索引.md#clean-sub-full-astra-low-20260913 "原路径：output/stage1.md")、[分镜Excel（本机归档）](../../docs/验证材料索引.md#clean-sub-full-astra-low-20260913 "原路径：output/shotlist.xlsx")、[Stage2固定条件（本机归档）](../../docs/验证材料索引.md#clean-sub-full-astra-low-20260913 "原路径：output/stage2.md")、[导出失败日志（本机归档）](../../docs/验证材料索引.md#clean-sub-full-astra-low-20260913 "原路径：logs/stage3-export.txt")。

原始input与冻结skill的文件指纹核对未变；source-current与当前源剧本逐字一致，Stage3首稿与最终保留稿逐字一致。此结果是本次完整场景及现有工具范围的结项依据，不保证所有场景零错误，也不包含视频执行效果。
