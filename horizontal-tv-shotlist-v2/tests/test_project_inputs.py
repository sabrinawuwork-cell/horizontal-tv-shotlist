"""Exercise project policy through real files and the three public entry points."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_v2_delivery as delivery_fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from v2_delivery_checks import digest
from validate_v2_shotlist import validate_file as check1
from validate_v2_stage2 import validate_file as check2
from validate_v2_prompts import validate_stage3 as check3, light_errors


class ProjectInputChecks(unittest.TestCase):
    # Reuse only the valid three-stage data builder, not its test methods.
    setUp = delivery_fixtures.DeliveryChecks.setUp
    field = delivery_fixtures.DeliveryChecks.field
    row = delivery_fixtures.DeliveryChecks.row
    replace_first_dialogue = delivery_fixtures.DeliveryChecks.replace_first_dialogue
    bind_plan = delivery_fixtures.DeliveryChecks.bind_plan

    def add_fields(self, path, **fields):
        path.write_text(path.read_text().replace('\n---\n',
            ''.join(f'\n{key}: {value}' for key, value in fields.items()) + '\n---\n', 1))

    def sync_downstream(self):
        self.field(self.s2, 'stage1_sha256', digest(self.s1))
        self.bindings['source_stage1_sha256'] = digest(self.s1)
        self.bindings_path.write_text(json.dumps(self.bindings, ensure_ascii=False))
        self.field(self.s3, 'stage1_sha256', digest(self.s1))
        self.field(self.s3, 'stage2_sha256', digest(self.s2))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))

    def bind_inputs(self, policy=None, *, inputs=None):
        directory = self.root / 'config'
        directory.mkdir(exist_ok=True)
        manifest = directory / 'inputs.json'
        manifest.write_text(json.dumps({'inputs': inputs or [], 'policy': policy or {}}, ensure_ascii=False))
        self.add_fields(self.s1, input_manifest='config/inputs.json', input_manifest_sha256=digest(manifest))
        self.sync_downstream()
        return manifest

    def assert_rejected(self, check, path, message):
        errors = check(path)
        self.assertTrue(any(message in error for error in errors), errors)

    def assert_cli(self, stage, *, error=None):
        if stage == 3:
            command = [sys.executable, str(ROOT / 'scripts/validate_v2_prompts.py'), '--stage3', str(self.s3)]
        else:
            name = 'validate_v2_shotlist.py' if stage == 1 else 'validate_v2_stage2.py'
            command = [sys.executable, str(ROOT / 'scripts' / name), str(self.s1 if stage == 1 else self.s2)]
        result = subprocess.run(command, capture_output=True, text=True, cwd=self.root, timeout=20)
        if error is None:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(error, result.stdout)

    def add_light(self, body):
        self.s2.write_text(self.s2.read_text() + '\n### 光影·甲\n\n' + body + '\n')
        self.bindings['shots']['1A']['fixed_cards'] = ['光影·甲']
        self.row('1A', 4, lambda text: text.replace('<br><br>无配乐', '<br><br>' + body + '<br><br>无配乐'))
        self.sync_downstream()

    def add_note(self, note):
        lines = []
        for line in self.s3.read_text().splitlines():
            if line.startswith('| 镜号 |'):
                line = line.rstrip() + ' 备注 |'
            elif line.startswith('| --- |'):
                line = line.rstrip() + ' --- |'
            elif line.startswith('| 1'):
                line = line.rstrip() + (' ' + note + ' |' if line.startswith('| 1A |') else ' |')
            lines.append(line)
        self.s3.write_text('\n'.join(lines) + '\n')

    def test_existing_valid_delivery_needs_no_fabricated_input_inventory(self):
        for stage in (1, 2, 3):
            self.assert_cli(stage)

    def test_scene_facts_declarations_must_be_paired(self):
        facts = self.root / 'facts.md'
        facts.write_text('桌高0.75米。')
        original = self.s1.read_text()
        for fields in ({'scene_facts': 'facts.md'}, {'scene_facts_sha256': digest(facts)}):
            with self.subTest(fields=fields):
                self.s1.write_text(original)
                self.add_fields(self.s1, **fields)
                self.assert_rejected(check1, self.s1, '必须成对声明')

    def test_scene_facts_edit_invalidates_all_three_actual_clis(self):
        facts = self.root / 'facts.md'
        facts.write_text('桌高0.75米。')
        self.add_fields(self.s1, scene_facts='facts.md', scene_facts_sha256=digest(facts))
        self.sync_downstream()
        for stage in (1, 2, 3):
            self.assert_cli(stage)
        facts.write_text('桌高1.4米。')
        for stage in (1, 2, 3):
            self.assert_cli(stage, error='scene_facts摘要')

    def test_manifest_uses_its_own_directory_and_pins_each_declared_role(self):
        directory = self.root / 'config'
        directory.mkdir()
        files = []
        for role in ('scene_facts', 'character', 'prop', 'production'):
            path = directory / (role + '.txt')
            path.write_text(role + '当前约定')
            # A same-name decoy beside Stage 1 must not change resolution.
            (self.root / path.name).write_text('错误的同名输入')
            files.append({'path': path.name, 'role': role, 'sha256': digest(path)})
        self.bind_inputs(inputs=files)
        self.assertEqual(check3(self.s3), [])
        for item in files:
            path = directory / item['path']
            original = path.read_bytes()
            with self.subTest(role=item['role']):
                path.write_text('输入已改动')
                for check, stage in ((check1, self.s1), (check2, self.s2), (check3, self.s3)):
                    self.assert_rejected(check, stage, 'input_manifest 第')
            path.write_bytes(original)
        (directory / files[0]['path']).unlink()
        self.assert_rejected(check3, self.s3, '文件不存在')

    def test_manifest_pair_and_manifest_policy_digest_are_checked(self):
        manifest = self.bind_inputs({'lighting_mode': 'descriptive'})
        original = self.s1.read_text()
        self.s1.write_text('\n'.join(line for line in original.splitlines() if not line.startswith('input_manifest_sha256:')) + '\n')
        self.assert_rejected(check1, self.s1, '必须成对声明')
        self.s1.write_text(original)
        manifest.write_text(json.dumps({'inputs': [], 'policy': {'lighting_mode': 'numeric'}}))
        for check, stage in ((check1, self.s1), (check2, self.s2), (check3, self.s3)):
            self.assert_rejected(check, stage, 'input_manifest摘要')

    def test_manifest_requires_known_policy_and_well_formed_inputs(self):
        manifest = self.bind_inputs()
        for payload, error in (
            ({'policy': {}}, 'inputs 数组'),
            ({'inputs': [None]}, 'path 与 sha256'),
            ({'inputs': [{'path': str(self.root / 'source.md'), 'sha256': digest(self.root / 'source.md')}]}, '相对清单文件'),
            ({'inputs': [], 'policy': {'lighting_mode': 'guess'}}, 'descriptive 或 numeric'),
            ({'inputs': [], 'policy': {'sensitive_terms': '医护'}}, '非空词语的数组'),
            ({'inputs': [], 'policy': {'lighting': 'numeric'}}, '未知字段'),
        ):
            with self.subTest(payload=payload):
                manifest.write_text(json.dumps(payload, ensure_ascii=False))
                self.field(self.s1, 'input_manifest_sha256', digest(manifest))
                self.assert_rejected(check1, self.s1, error)

    def test_refreshed_manifest_does_not_silently_refresh_downstream_bindings(self):
        directory = self.root / 'config'
        directory.mkdir()
        facts = directory / 'facts.md'
        facts.write_text('桌高0.75米。')
        manifest = self.bind_inputs(inputs=[{'path': 'facts.md', 'sha256': digest(facts)}])
        facts.write_text('桌高1.4米。')
        manifest.write_text(json.dumps({'inputs': [{'path': 'facts.md', 'sha256': digest(facts)}]}))
        self.field(self.s1, 'input_manifest_sha256', digest(manifest))
        self.assertEqual(check1(self.s1), [])
        self.assert_rejected(check2, self.s2, 'Stage 1摘要')
        self.assert_rejected(check3, self.s3, 'Stage 1摘要')
        self.sync_downstream()
        self.assertEqual(check3(self.s3), [])

    def test_director_plan_declared_inputs_must_match_stage1(self):
        plan = self.bind_plan()
        facts = self.root / 'facts.md'
        other = self.root / 'other-facts.md'
        facts.write_text('桌高0.75米。')
        other.write_text('桌高1.4米。')
        self.add_fields(self.s1, scene_facts='facts.md', scene_facts_sha256=digest(facts))
        self.add_fields(plan, scene_facts='other-facts.md', scene_facts_sha256=digest(other))
        self.field(self.s1, 'director_plan_sha256', digest(plan))
        self.assert_rejected(check1, self.s1, 'scene_facts 绑定不是同一文件')

    def test_descriptive_light_is_valid_without_project_numeric_requirement(self):
        self.add_light('光影：主光源为甲左侧窗，左侧脸明亮，右侧落入柔暗部。')
        self.assert_cli(2)
        self.assert_cli(3)
        self.assertTrue(light_errors('光影：主光源为甲左侧窗，左侧脸明亮，右侧落入柔暗部。'))

    def test_uniform_panel_light_does_not_require_shadow_or_direction_keywords(self):
        self.add_light('光影：天花板固定柔和面板灯，均匀中性照明，无窗外光或光源变化。')
        self.assert_cli(2)
        self.assert_cli(3)

    def test_diffuse_daylight_does_not_require_a_directional_key_light(self):
        self.add_light('光影：阴天漫射，肤色自然，人物与周围环境曝光均匀。')
        self.assert_cli(2)
        self.assert_cli(3)

    def test_numeric_light_inherits_exports_and_revises_without_word_list(self):
        self.bind_inputs({'lighting_mode': 'numeric'})
        light = '光影：5600K天光自头顶落下，人物额头亮面200lux，下颌暗面50lux。'
        self.add_light(light)
        self.assert_cli(2)

        def export(name, error=None):
            output = self.root / name
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/generation_tasks.py'),
                '--stage3', str(self.s3), '--output-dir', str(output)],
                capture_output=True, text=True, cwd=self.root, timeout=20)
            if error is not None:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(error, result.stdout)
                self.assertFalse(output.exists())
                return ''
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return (output / 'task-001.txt').read_text()

        self.assertIn(light, export('original'))
        stage3_before = self.s3.read_text()
        for name, replacement in (('omitted', ''), ('changed', light.replace('额头', '下颌'))):
            with self.subTest(name=name):
                self.row('1A', 4, lambda text: text.replace(light, replacement))
                export(name, '固定条件正文')
                self.s3.write_text(stage3_before)

        revised = light.replace('200lux', '160lux')
        self.s2.write_text(self.s2.read_text().replace(light, revised))
        export('stale', 'Stage 2摘要')
        self.sync_downstream()
        export('unsynced_card', '固定条件正文')
        self.row('1A', 4, lambda text: text.replace(light, revised))
        result = export('revised')
        self.assertIn(revised, result)
        self.assertNotIn(light, result)
        self.assertIn('第二句。', result)

    def test_grounded_camera_relative_light_is_not_a_prohibited_meta_command(self):
        self.add_light('光影：主光为北窗，甲面向东，摄影机在甲的南侧；甲自身左侧受北窗照亮，'
            '远离摄影机的一侧是亮面，靠近摄影机的一侧落入阴影。')
        self.assert_cli(2)
        self.assert_cli(3)

    def test_numeric_light_still_requires_kelvin_and_two_illuminance_values(self):
        self.bind_inputs({'lighting_mode': 'numeric'})
        self.add_light('光影：5600K天光自头顶落下，额头亮面200lux，下颌暗面50lux。')
        original = self.s2.read_text()
        for removed, diagnostic in (('5600K', '色温K'), ('50lux', 'lux')):
            with self.subTest(removed=removed):
                self.s2.write_text(original.replace(removed, ''))
                self.sync_downstream()
                self.assert_rejected(check2, self.s2, diagnostic)
                self.assert_rejected(check3, self.s3, diagnostic)

    def test_project_numeric_light_requirement_is_inherited_by_both_stages(self):
        self.bind_inputs({'lighting_mode': 'numeric'})
        self.add_light('光影：主光源为甲左侧窗，左侧脸明亮，右侧落入柔暗部。')
        self.assert_cli(2, error='色温K')
        self.assert_cli(3, error='色温K')
        # The upstream card must be checked even if it is not selected below.
        self.bindings['shots']['1A']['fixed_cards'] = []
        self.row('1A', 4, lambda text: text.replace('<br><br>光影：主光源为甲左侧窗，左侧脸明亮，右侧落入柔暗部。', ''))
        self.sync_downstream()
        self.assert_rejected(check3, self.s3, '色温K')
        self.s2.write_text(self.s2.read_text().replace('左侧脸明亮，右侧落入柔暗部。', '4000K，左侧脸300 lux亮面，右侧100 lux暗部。'))
        self.sync_downstream()
        self.assert_cli(2)
        self.assert_cli(3)

    def test_descriptive_lighting_quality_requires_content_review(self):
        self.add_light('光影：氛围梦幻，保持far-side key。')
        self.assertEqual(check2(self.s2), [])  # 通过结构检查不证明光影描述充分
        self.assertEqual(check3(self.s3), [])

    def test_existing_numeric_light_without_qualitative_adjectives_remains_valid(self):
        self.bind_inputs({'lighting_mode': 'numeric'})
        self.add_light('光影：主光源为甲左侧窗，4000K，左侧300 lux，右侧100 lux。')
        self.assertEqual(check2(self.s2), [])
        self.assertEqual(check3(self.s3), [])

    def test_project_terms_and_existing_authorizations_protect_original_dialogue(self):
        self.replace_first_dialogue('医护正在等待红军。')
        manifest = self.bind_inputs({'sensitive_terms': ['医护'], 'dialogue_sensitive_authorizations': ['医护', '红军']})
        before = {path: path.read_bytes() for path in (self.root / 'source.md', self.s1, self.s3)}
        self.assert_cli(3)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.row('1A', 4, lambda text: text.replace('画面：甲', '画面：医护甲'))
        self.assert_rejected(check3, self.s3, '非对白敏感词未处理：医护')
        self.s3.write_bytes(before[self.s3])
        manifest.write_text(json.dumps({'inputs': [], 'policy': {'sensitive_terms': ['医护']}}))
        self.field(self.s1, 'input_manifest_sha256', digest(manifest))
        self.sync_downstream()
        self.assert_rejected(check3, self.s3, '对白敏感词缺少授权：医护')
        self.field(self.s3, 'dialogue_sensitive_authorizations', '医护,红军')
        self.assert_cli(3)

    def test_stage2_project_term_is_checked_even_in_an_unselected_card(self):
        self.bind_inputs({'sensitive_terms': ['医护']})
        self.s2.write_text(self.s2.read_text() + '\n### 背景·门\n\n背景：医护专用的门。\n')
        self.sync_downstream()
        self.assert_rejected(check2, self.s2, '非对白敏感词未处理：医护')
        self.assert_rejected(check3, self.s3, '非对白敏感词未处理：医护')

    def test_stage3_override_cannot_choose_a_different_policy(self):
        self.bind_inputs({'sensitive_terms': ['医护']})
        (self.root / 'other-inputs.json').write_text(json.dumps({'inputs': [], 'policy': {}}))
        self.add_fields(self.s3, input_manifest='other-inputs.json', input_manifest_sha256=digest(self.root / 'other-inputs.json'))
        self.row('1A', 4, lambda text: text.replace('画面：甲', '画面：医护甲'))
        self.assert_rejected(check3, self.s3, '非对白敏感词未处理：医护')

    def test_new_latin_terms_match_words_without_banning_ordinary_substrings(self):
        self.bind_inputs({'sensitive_terms': ['ER']})
        self.row('1A', 4, lambda text: text.replace('画面：甲', '画面：甲的标识为WORKER，甲'))
        self.assertEqual(check3(self.s3), [])
        self.row('1A', 4, lambda text: text.replace('WORKER', 'ER'))
        self.assert_rejected(check3, self.s3, '非对白敏感词未处理：ER')

    def test_approved_stage3_rejects_red_blocker_but_keeps_yellow_behavior(self):
        original = self.s3.read_text()
        self.add_note('🔴【执行阻塞】所需参考资产缺失，无法执行。')
        self.assert_cli(3, error='🔴阻塞')
        self.s3.write_text(original)
        self.add_note('🟡【情感待确认】采用既有规则允许的保守情感解释。')
        self.assert_cli(3)

    def test_red_blocker_in_body_is_rejected_but_quoted_dialogue_is_protected(self):
        self.replace_first_dialogue('这个标记是🔴【执行阻塞】。')
        self.assertEqual(check3(self.s3), [])
        self.s3.write_text(self.s3.read_text() + '\n- 🔴【执行阻塞】尚缺必要道具资产。\n')
        self.assert_rejected(check3, self.s3, '🔴阻塞')

    def test_explicit_frontmatter_red_blocker_is_rejected(self):
        self.add_fields(self.s3, blocking_issues='🔴【执行阻塞】尚缺必要道具资产。')
        self.assert_rejected(check3, self.s3, '🔴阻塞')


if __name__ == '__main__':
    unittest.main()
