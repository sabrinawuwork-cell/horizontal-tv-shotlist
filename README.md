# 横屏剧集分镜导演 V2

本仓库只维护 [horizontal-tv-shotlist-v2](horizontal-tv-shotlist-v2/SKILL.md)。使用、修改和安装均以该目录为入口；根目录旧版已删除。

当前 V2 来自 `a7968ef`（PR #10：Update V2 and add anonymous forward test）。V2 内继承的格式规则、示例和兼容测试属于当前包的组成部分，不需要单独的 V1 项目。

- 技能入口：[V2 SKILL.md](horizontal-tv-shotlist-v2/SKILL.md)
- 匿名案例：[社区听证室](forward-tests/anonymous-hearing-room-20260813/forward-test-report.md)
- 决策与验证记录：[决策与变更](docs/决策与变更.md)

## 本地验证

在仓库根目录执行：

```sh
python3 -m unittest discover -s horizontal-tv-shotlist-v2/tests
python3 horizontal-tv-shotlist-v2/scripts/validate_v1_compatibility.py
python3 horizontal-tv-shotlist-v2/scripts/validate_seedance_rules_master.py horizontal-tv-shotlist-v2/references/v2-seedance-prompt-rules-master.md
python3 horizontal-tv-shotlist-v2/scripts/validate_v2_prompts.py --skill-root horizontal-tv-shotlist-v2 --self-test
python3 forward-tests/anonymous-hearing-room-20260813/verify_forward_test.py
```

这些检查验证规则完整性、结构和案例中的显式条件，不替代导演判断与实际视频生成验收。

## 构建安装包

```sh
python3 horizontal-tv-shotlist-v2/scripts/build_distribution.py --output dist/horizontal-tv-shotlist-v2
```

“情感解析”的后续处理仍待用户决定，本次版本整理不改变相关规则。
