from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_project_state
import validate_stage2_fixed_prompts
import validate_seedance_rules_master
import expand_stage3_fixed_prompts
import validate_prompt_mapping
import validate_shotlist


FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples"


class ShotlistValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (FIXTURES / "sample-shotlist.md").read_text(encoding="utf-8")

    def test_valid_sample_and_coverage_exclusion(self) -> None:
        errors, summary = validate_shotlist.validate_shotlist_text(
            self.text, dialogue_manifest=FIXTURES / "dialogue.json", asset_root=FIXTURES
        )
        self.assertEqual(errors, [])
        self.assertEqual(summary["main_shots"], 3)
        self.assertEqual(summary["coverage_shots"], 1)
        self.assertEqual(summary["total_duration"], "18.5s")

    def test_rejects_legacy_columns(self) -> None:
        broken = self.text.replace(
            "| 场次 | 镜号 | 时长 | 摄影机 | 画面内容 | 音效 | 剪辑衔接 |",
            "| 场次 | 镜号 | 时长 | 景别 | 机位/角度 | 运镜/调度 | 画面内容 | 音效 | 剪辑衔接 |",
            1,
        )
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("七列表头必须精确" in error for error in errors))

    def test_rejects_over_thirty_seconds(self) -> None:
        broken = self.text.replace("| 1 | 1A | 6s |", "| 1 | 1A | 31s |")
        broken = broken.replace("total_duration: 18.5s", "total_duration: 43.5s")
        broken = broken.replace("场次时长：18.5s", "场次时长：43.5s")
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("超过 30s" in error for error in errors))

    def test_twenty_to_thirty_requires_shot_authorization(self) -> None:
        broken = self.text.replace("| 1 | 1A | 6s |", "| 1 | 1A | 22s |")
        broken = broken.replace("total_duration: 18.5s", "total_duration: 34.5s")
        broken = broken.replace("场次时长：18.5s", "场次时长：34.5s")
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("缺少镜号级授权" in error for error in errors))
        authorized = broken.replace("long_shot_authorizations: none", "long_shot_authorizations: 1A")
        errors, _summary = validate_shotlist.validate_shotlist_text(authorized)
        self.assertEqual(errors, [])

    def test_rejects_missing_numeric_focal_length(self) -> None:
        broken = self.text.replace("50mm，摄影机位于桌南侧", "中焦，摄影机位于桌南侧", 1)
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("具体毫米焦段" in error for error in errors))

    def test_rejects_camera_jargon_and_dirty_labels(self) -> None:
        for phrase in ("50mm全画幅等效", "门侧半轴", "脏单人", "干净单人", "脏前景"):
            with self.subTest(phrase=phrase):
                broken = self.text.replace("50mm", phrase, 1)
                errors, _summary = validate_shotlist.validate_shotlist_text(
                    broken, asset_root=FIXTURES
                )
                self.assertTrue(any("禁止的解释性或分类术语" in error for error in errors))

    def test_rejects_tight_two_shot_label(self) -> None:
        broken = self.text.replace("艾拉与凯尔双人中景", "艾拉与凯尔紧双人", 1)
        errors, _summary = validate_shotlist.validate_shotlist_text(
            broken, asset_root=FIXTURES
        )
        self.assertTrue(any("双人景别只允许" in error for error in errors))

    def test_rejects_visible_analysis_and_continuity_labels(self) -> None:
        broken = self.text.replace(
            "艾拉坐在画面左侧",
            "主要功能：强化关系；第一主体为艾拉。艾拉坐在画面左侧",
            1,
        ).replace(
            "文件停住时反应切近艾拉。",
            "文件停住时反应切近艾拉。连续性：桌面轴不变。",
            1,
        )
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("混入分析标签" in error for error in errors))
        self.assertTrue(any("不得展示连续性备注" in error for error in errors))

    def test_rejects_visible_active_cast_count(self) -> None:
        broken = self.text.replace(
            "艾拉坐在画面左侧",
            "主动人物：艾拉、凯尔。艾拉坐在画面左侧",
            1,
        )
        errors, _summary = validate_shotlist.validate_shotlist_text(broken, asset_root=FIXTURES)
        self.assertTrue(any("混入分析标签" in error for error in errors))

    def test_rejects_named_invisible_pov_owner(self) -> None:
        text = (EXAMPLES / "suspense-scene.md").read_text(encoding="utf-8")
        broken = text.replace("POV中景", "玛雅的POV中景", 1)
        errors, _summary = validate_shotlist.validate_shotlist_text(broken, asset_root=EXAMPLES)
        self.assertTrue(any("POV 摄影机不得写" in error for error in errors))

    def test_rejects_other_pov_shot_sizes(self) -> None:
        text = (EXAMPLES / "suspense-scene.md").read_text(encoding="utf-8")
        broken = text.replace("POV中景", "POV极特写", 1)
        errors, _summary = validate_shotlist.validate_shotlist_text(broken, asset_root=EXAMPLES)
        self.assertTrue(any("POV中景" in error for error in errors))

    def test_rejects_absolute_source_path(self) -> None:
        broken = self.text.replace("source: sample-script.txt", "source: /Users/private/sample-script.txt")
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("不得泄漏本机绝对路径" in error for error in errors))

    def test_rejects_missing_scene_image_asset(self) -> None:
        broken = self.text.replace("assets/office-scene.svg", "assets/missing-scene.svg")
        errors, _summary = validate_shotlist.validate_shotlist_text(broken, asset_root=FIXTURES)
        self.assertTrue(any("场景图文件不存在" in error for error in errors))

    def test_rejects_dialogue_in_effects(self) -> None:
        broken = self.text.replace("| 衣袖摩擦。 |", "| Aira: “I know what you did.” |")
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("对白必须放在画面内容" in error for error in errors))

    def test_rejects_coverage_without_base(self) -> None:
        broken = self.text.replace("1B-COV1", "1D-COV1")
        errors, _summary = validate_shotlist.validate_shotlist_text(broken)
        self.assertTrue(any("基础主镜 1D 不存在" in error for error in errors))

    def test_three_stage_one_examples_validate(self) -> None:
        for name in ("dialogue-scene.md", "suspense-scene.md", "action-scene.md"):
            with self.subTest(name=name):
                errors, summary = validate_shotlist.validate_shotlist_text(
                    (EXAMPLES / name).read_text(encoding="utf-8"), asset_root=EXAMPLES
                )
                self.assertEqual(errors, [])
                self.assertGreater(summary["main_shots"], 1)


class PromptMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (FIXTURES / "sample-shotlist.md").read_text(encoding="utf-8").replace("status: draft", "status: approved")
        self.stage2 = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        self.draft = (FIXTURES / "sample-stage3-draft.md").read_text(encoding="utf-8")

    def test_valid_three_stage_mapping_and_mechanical_expansion(self) -> None:
        approved, expansion_errors = expand_stage3_fixed_prompts.expand_text(self.draft, [self.stage2])
        self.assertEqual(expansion_errors, [])
        errors = validate_prompt_mapping.validate_mapping(
            self.source, [self.stage2], self.draft, approved
        )
        self.assertEqual(errors, [])
        self.assertNotIn("〔", approved)
        self.assertIn("照亮艾拉左侧脸颊", approved)

    def test_rejects_stage1_camera_or_dialogue_rewrite(self) -> None:
        broken = self.draft.replace("85mm", "18mm")
        broken = broken.replace("I know what you did.", "I believe you.")
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("未逐字保留 Stage 1 画面内容" in error for error in errors))
        self.assertTrue(any("未保留 Stage 1 焦段 85mm" in error for error in errors))

    def test_rejects_stage1_edit_transition_in_stage3(self) -> None:
        broken = self.draft.replace(
            "<br><br>固定类别：〔光影·艾拉与凯尔〕",
            "<br><br>剪辑：文件停住时反应切近艾拉。<br><br>固定类别：〔光影·艾拉与凯尔〕",
            1,
        )
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("不得包含剪辑类别" in error for error in errors))

    def test_rejects_missing_double_shot_visible_range(self) -> None:
        broken = self.draft.replace("，两人头部到腰部可见", "", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("双人中景" in error and "头部到腰部" in error for error in errors))

    def test_rejects_focal_length_out_of_master_order(self) -> None:
        broken = self.draft.replace("0-6秒。50mm，艾拉与凯尔双人中景", "0-6秒。艾拉与凯尔双人中景，50mm", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("累计时间后首先写焦段" in error for error in errors))

    def test_rejects_incomplete_over_shoulder_control(self) -> None:
        broken = self.draft.replace("凯尔左肩虚化在画面右侧前景，", "", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("过肩提示词缺少" in error for error in errors))

    def test_rejects_shot_split_or_duplicate(self) -> None:
        broken = self.draft.replace("| 1B | 片段1 | 4.5s |", "| 1A | 片段1 | 4.5s |")
        errors = validate_prompt_mapping.validate_mapping(
            self.source, [self.stage2], broken
        )
        self.assertTrue(any("完整、唯一且顺序一致" in error for error in errors))

    def test_rejects_segment_over_thirty(self) -> None:
        broken_source = self.source.replace("| 1 | 1C | 8s |", "| 1 | 1C | 20s |").replace("total_duration: 18.5s", "total_duration: 30.5s").replace("场次时长：18.5s", "场次时长：30.5s")
        broken = self.draft.replace("| 1C | 片段1 | 8s |", "| 1C | 片段1 | 20s |", 1).replace("10.5-18.5秒。", "10.5-30.5秒。", 1)
        errors = validate_prompt_mapping.validate_mapping(
            broken_source, [self.stage2], broken
        )
        self.assertTrue(any("超过30s硬上限" in error for error in errors))

    def test_rejects_wrong_segment_cumulative_time(self) -> None:
        broken = self.draft.replace("6-10.5秒。", "0-4.5秒。", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("第一句必须为 6-10.5秒" in error for error in errors))

    def test_rejects_seedance25_format_under_other_model(self) -> None:
        broken = self.draft.replace("model: seedance-2.5", "model: seedance-2.0", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("只允许 model: seedance-2.5" in error for error in errors))

    def test_rejects_storyboard_without_blank_category_lines(self) -> None:
        broken = self.draft.replace("。<br><br>画面内容：", "。<br>画面内容：", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("分镜内容三类别之间必须空一行" in error for error in errors))

    def test_rejects_legacy_scene_column(self) -> None:
        broken = self.draft.replace(
            "| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 |",
            "| 场次 | 片段 | 镜号 | 时长 | Seedance提示词 |",
            1,
        )
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("表头必须精确" in error for error in errors))

    def test_conditional_notes_column(self) -> None:
        lines = self.draft.splitlines()
        output = []
        for line in lines:
            if line == "| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 |":
                output.append("| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 | 备注 |")
            elif line == "| --- | --- | --- | --- | --- |":
                output.append("| --- | --- | --- | --- | --- | --- |")
            elif line.startswith("| 1A |"):
                output.append(line[:-2] + " | 🟠【主体风险】当前片段累计9个主体。<br>🟡【情感待确认】艾拉此处更偏拒绝还是试探？ |")
            elif line.startswith(("| 1B |", "| 1C |")):
                output.append(line[:-2] + " |  |")
            else:
                output.append(line)
        noted = "\n".join(output) + "\n"
        self.assertEqual(validate_prompt_mapping.validate_mapping(self.source, [self.stage2], noted), [])

        blank = noted.replace("🟠【主体风险】当前片段累计9个主体。<br>🟡【情感待确认】艾拉此处更偏拒绝还是试探？", "")
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], blank)
        self.assertTrue(any("不得保留空的备注列" in error for error in errors))

    def test_rejects_legacy_note_format(self) -> None:
        lines = self.draft.splitlines()
        output = []
        for line in lines:
            if line == "| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 |":
                output.append("| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 | 备注 |")
            elif line == "| --- | --- | --- | --- | --- |":
                output.append("| --- | --- | --- | --- | --- | --- |")
            elif line.startswith("| 1A |"):
                output.append(line[:-2] + " | 待确认：〔旧格式〕 |")
            elif line.startswith(("| 1B |", "| 1C |")):
                output.append(line[:-2] + " |  |")
            else:
                output.append(line)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], "\n".join(output) + "\n")
        self.assertTrue(any("备注每个问题必须逐行使用" in error for error in errors))

    def test_rejects_segment_subject_note_after_first_shot(self) -> None:
        lines = self.draft.splitlines()
        output = []
        for line in lines:
            if line == "| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 |":
                output.append("| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 | 备注 |")
            elif line == "| --- | --- | --- | --- | --- |":
                output.append("| --- | --- | --- | --- | --- | --- |")
            elif line.startswith("| 1B |"):
                output.append(line[:-2] + " | 🟠【主体风险】当前片段累计9个主体。 |")
            elif line.startswith(("| 1A |", "| 1C |")):
                output.append(line[:-2] + " |  |")
            else:
                output.append(line)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], "\n".join(output) + "\n")
        self.assertTrue(any("片段级主体问题只能写在片段首镜" in error for error in errors))

    def test_rejects_unknown_category_and_nonmechanical_approved(self) -> None:
        broken = self.draft.replace("〔光影·艾拉与凯尔〕", "〔光影·虚构方向〕", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("未知固定类别" in error for error in errors))
        approved, _ = expand_stage3_fixed_prompts.expand_text(self.draft, [self.stage2])
        changed = approved.replace("凯尔仍坐", "凯尔缓慢站起", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], self.draft, changed)
        self.assertTrue(any("不是 draft 的固定类别机械展开结果" in error for error in errors))

    def test_allows_explicit_no_fixed_category(self) -> None:
        draft = self.draft.replace("固定类别：〔光影·艾拉与凯尔〕", "固定类别：无", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], draft)
        self.assertEqual(errors, [])

    def test_rejects_missing_no_marker_when_no_fixed_category(self) -> None:
        broken = self.draft.replace("固定类别：〔光影·艾拉与凯尔〕", "固定类别：", 1)
        errors = validate_prompt_mapping.validate_mapping(self.source, [self.stage2], broken)
        self.assertTrue(any("固定类别：无" in error for error in errors))


class Stage2ValidationTests(unittest.TestCase):
    def test_valid_stage2(self) -> None:
        stage2_text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        categories, errors, values = validate_stage2_fixed_prompts.category_map(stage2_text)
        self.assertEqual(errors, [])
        self.assertEqual(values["status"], "approved")
        self.assertIn("光影·艾拉与凯尔", categories)
        self.assertFalse(any(name.startswith("背景·") for name in categories))
        self.assertNotIn("无配乐无字幕", "".join(categories.values()))
        self.assertEqual(
            validate_stage2_fixed_prompts.validate_against_stage1(
                stage2_text,
                (FIXTURES / "sample-shotlist.md").read_text(encoding="utf-8"),
                source_name="sample-shotlist.md",
            ),
            [],
        )

    def test_rejects_missing_voice_but_does_not_require_independent_background(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text = re.sub(
            r"\n### 基础音色·艾拉\n.*?(?=\n### )",
            "\n",
            text,
            flags=re.S,
        )
        _categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertTrue(any("缺少基础音色" in error for error in errors))
        self.assertFalse(any("至少需要" in error and "背景" in error for error in errors))

    def test_rejects_shot_number_category_name(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text = text.replace("光影·艾拉与凯尔", "光影·1A")
        _categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertTrue(any("不得包含镜号" in error for error in errors))

    def test_rejects_stage3_only_tail_inside_stage2(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text = text.replace(
            "稳定听感是敏感而克制。",
            "稳定听感是敏感而克制。无配乐无字幕 视频里不出现红蓝色标记",
        )
        _categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertTrue(any("只允许由 Stage 3" in error for error in errors))
        self.assertTrue(any("临时限制不得进入 Stage 2" in error for error in errors))

    def test_scene_or_phase_voice_is_optional(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertEqual(errors, [])
        self.assertIn("基础音色·艾拉", categories)
        self.assertFalse(any(name.startswith(("场次音色·", "阶段音色·")) for name in categories))

    def test_rejects_generic_skin_phrase_inside_stage2(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text = text.replace(
            "整体低照度、中高反差、冷静克制的真实电影质感。",
            "整体低照度、中高反差、冷静克制的真实电影质感。保留真实的微细毛孔、肌肤纹理与皮肤质感。",
            1,
        )
        _categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertTrue(any("通用皮肤质感句由 Stage 3" in error for error in errors))

    def test_rejects_legacy_two_column_table(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text += "\n\n| 固定类别 | 固定提示词 |\n| --- | --- |\n| 光影·旧格式 | 光影：旧格式 |\n"
        _categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertTrue(any("不再使用固定提示词两列表格" in error for error in errors))

    def test_rejects_legacy_composite_visual(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text += "\n\n### 固定视觉·旧复合包\n\n光影：旧规则。\n"
        _categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertTrue(any("未知固定类别前缀" in error for error in errors))

    def test_allows_scene_with_no_fixed_cards(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text = text.replace("speaking_characters: 艾拉", "speaking_characters: none")
        text = re.sub(r"\n### 基础音色·艾拉\n.*", "\n\n# 场次 1 极简固定提示词\n\n本场无须建立固定类别。\n", text, flags=re.S)
        categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertEqual(errors, [])
        self.assertEqual(categories, {})

    def test_rejects_light_without_numeric_minimums(self) -> None:
        text = (FIXTURES / "sample-stage2-approved.md").read_text(encoding="utf-8")
        text = text.replace("260–320 lux", "柔和亮面", 1).replace("90–130 lux", "柔暗部", 1)
        _categories, errors, _values = validate_stage2_fixed_prompts.category_map(text)
        self.assertTrue(any("亮面、暗面和背景 lux" in error for error in errors))


class SeedanceRulesMasterTests(unittest.TestCase):
    def test_master_is_byte_identical_to_user_authority(self) -> None:
        master = ROOT / "references" / "seedance-prompt-rules-master.md"
        self.assertEqual(validate_seedance_rules_master.validate(master), [])

    def test_seedance25_subject_budget_rules_are_present(self) -> None:
        text = (ROOT / "references" / "model-seedance-2.5.md").read_text(encoding="utf-8")
        for phrase in (
            "片段累计主体按“唯一身份”计算",
            "`1–8`个主体：稳定区间",
            "`9–12`个主体：风险区间",
            "超过`12`个主体",
            "具有独立参考图",
            "功能性人物或人物道具",
            "超过`5`个主体时优先使用单视图",
            "同一身份的多张角度图仍只计一个主体",
        ):
            self.assertIn(phrase, text)


class ProjectStateTests(unittest.TestCase):
    def test_state_requires_approved_source_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "north-star-E01-approved.md"
            text = (FIXTURES / "sample-shotlist.md").read_text(encoding="utf-8")
            approved.write_text(text, encoding="utf-8")
            digest = hashlib.sha256(approved.read_bytes()).hexdigest()
            state = root / "north-star-project-state-v001.md"
            state.write_text(
                "---\nartifact: horizontal-tv-project-state\nproject: north-star\nversion: v001\napproved_through: E01\n---\n\n"
                "| 集号 | 已批准分镜 | SHA256 |\n| --- | --- | --- |\n"
                f"| E01 | {approved.name} | {digest} |\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_project_state.validate_state(state), [])
            approved.write_text(text.replace("status: approved", "status: draft"), encoding="utf-8")
            errors = validate_project_state.validate_state(state)
            self.assertTrue(any("不是 approved" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
