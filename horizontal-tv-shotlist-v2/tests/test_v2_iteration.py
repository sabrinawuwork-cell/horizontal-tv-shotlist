from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_v2_prompts
import validate_v2_stage2


class V2IterationValidationTests(unittest.TestCase):
    def test_voice_reference_is_opt_in(self) -> None:
        prompt = (
            "0-4秒。65mm，角色中近景，眼平固定镜头。\n\n"
            "画面：角色位于画面中央。\n\n"
            "动作/表情：角色向自身右侧转头，视线停在画面左侧。\n\n"
            "情感解析：转头后停住，表明新信息改变了当前注意力。\n\n"
            "声音：N/A。\n\n"
            "【角色名音色参考：】中低音声线。\n\n"
            "无配乐无字幕"
        )
        self.assertTrue(
            any("默认输出不得出现" in item for item in validate_v2_prompts.prompt_errors(prompt, set()))
        )
        self.assertFalse(
            any(
                "默认输出不得出现" in item
                for item in validate_v2_prompts.prompt_errors(
                    prompt, set(), allow_voice_reference=True
                )
            )
        )

    def test_picture_must_not_prewrite_landing_frame(self) -> None:
        prompt = (
            "0-4秒。50mm，角色中景，眼平固定镜头。\n\n"
            "画面：角色位于画面左侧，镜头最终停在其脸上。\n\n"
            "动作/表情：角色向自身右侧转头。\n\n"
            "声音：N/A。\n\n"
            "无配乐无字幕"
        )
        self.assertTrue(any("只能写起幅" in item for item in validate_v2_prompts.prompt_errors(prompt, set())))

    def test_dialogue_is_compared_against_stage1(self) -> None:
        stage1 = """---
artifact: horizontal-tv-shotlist
project: anonymous
episode: E01
status: approved
---

| 场次 | 镜号 | 时长 | 摄影机 | 画面内容 | 音效 | 剪辑衔接 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1A | 4s | 角色的中近景，65mm，机位在角色右前方，眼平固定镜头。 | 角色看向画面左侧时说：“保留这句原文。” | N/A | 动作完成后切镜。 |
"""
        prompt = (
            "0-4秒。65mm，角色中近景，机位在角色右前方，眼平固定镜头。"
            "<br><br>画面：角色位于画面中央。"
            "<br><br>动作/表情：角色向自身右侧、画面左侧转头，最终站在画面中央，视线停在画面左侧，身体停止运动。"
            "<br><br>声音：N/A。"
            "<br><br>无配乐无字幕"
        )
        stage3 = f"""---
artifact: horizontal-tv-stage3-v2
project: anonymous
episode: E01
version: v001
status: approved
source_stage1: stage1.md
source_stage2: stage2.md
model: seedance-2.5
segment_hard_limit: 30s
dialogue_sensitive_authorizations: none
---

| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 |
| --- | --- | --- | --- | --- |
| 1A | 片段1 | 4s | 摄影机：原文<br><br>画面内容：原文<br><br>音效：N/A | {prompt} |
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage1_path = root / "stage1.md"
            stage3_path = root / "stage3.md"
            stage1_path.write_text(stage1, encoding="utf-8")
            stage3_path.write_text(stage3, encoding="utf-8")
            errors = validate_v2_prompts.validate_stage3(stage3_path, stage1_path=stage1_path)
        self.assertTrue(any("遗漏或改写Stage 1原始台词" in item for item in errors))

    def test_anonymous_regressions_pass(self) -> None:
        self.assertEqual(validate_v2_prompts.run_self_tests(ROOT), [])

    def test_v2_stage2_defaults_to_no_voice_cards(self) -> None:
        text = (ROOT / "tests" / "fixtures" / "v2-stage2-no-voice-approved.md").read_text(encoding="utf-8")
        self.assertEqual(validate_v2_stage2.validate_text(text), [])
        broken = text + "\n### 基础音色·角色A\n\n音色：中低音声线。\n"
        self.assertTrue(
            any("未明确启用音色" in item for item in validate_v2_stage2.validate_text(broken))
        )


if __name__ == "__main__":
    unittest.main()
