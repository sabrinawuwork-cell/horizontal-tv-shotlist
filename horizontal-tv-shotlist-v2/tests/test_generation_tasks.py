"""Real-file and CLI checks for generation tasks and edit-order material reuse."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_v2_delivery as fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from v2_delivery_checks import digest
from validate_v2_shotlist import validate_file as check1
from validate_v2_prompts import validate_stage3 as check3
import generation_tasks as generation


class GenerationTaskChecks(unittest.TestCase):
    field = fixtures.DeliveryChecks.field
    row = fixtures.DeliveryChecks.row

    def setUp(self):
        fixtures.DeliveryChecks.setUp(self)
        self.extra_stage2 = None
        self.output = self.root / 'export'
        self.plan_path = self.root / 'plans' / 'generation.json'
        self.plan_path.parent.mkdir()
        self.evidence = '甲的表演持续，切乙的反应后回到甲，接续同一段表演。'
        source = self.root / 'source.md'
        source.write_text(source.read_text() + '甲继续说：“第三句。”')
        dialogue = self.root / 'dialogue.json'
        dialogue.write_text(json.dumps({'dialogue': ['第一句。', '第二句。', '第三句。'], 'source_speech': ['第一句。', '第二句。', '第三句。'], 'stage1_speech': {'1A': ['第一句。'], '1B': ['第二句。'], '1C': ['第三句。']}}, ensure_ascii=False))
        text1 = self.s1.read_text()
        first = next(line for line in text1.splitlines() if line.startswith('| 1 | 1A |'))
        last = first.replace('| 1A |', '| 1C |').replace('第一句。', '第三句。')
        text1 = text1.replace('场次时长：8s', '场次时长：12s').replace('台词结束后切镜。', self.evidence, 1)
        self.s1.write_text(text1 + last + '\n')
        self.field(self.s1, 'total_duration', '12s')
        self.field(self.s1, 'source_sha256', digest(source))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(dialogue))
        first3 = next(line for line in self.s3.read_text().splitlines() if line.startswith('| 1A |'))
        last3 = first3.replace('| 1A |', '| 1C |').replace('第一句。', '第三句。').replace('0-4秒。', '8-12秒。')
        self.s3.write_text(self.s3.read_text() + last3 + '\n')
        self.bindings['shots']['1C'] = {'fixed_cards': [], 'speech': ['第三句。']}
        self.sync()

    def sync(self):
        self.field(self.s2, 'stage1_sha256', digest(self.s1))
        stage2_hashes = [digest(self.s2)]
        if self.extra_stage2:
            self.field(self.extra_stage2, 'stage1_sha256', digest(self.s1))
            stage2_hashes.append(digest(self.extra_stage2))
        self.bindings['source_stage1_sha256'] = digest(self.s1)
        self.bindings_path.write_text(json.dumps(self.bindings, ensure_ascii=False))
        self.field(self.s3, 'stage1_sha256', digest(self.s1))
        self.field(self.s3, 'stage2_sha256', ','.join(stage2_hashes))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))

    def plan(self, *, reuse=True):
        if reuse:
            self.row('1B', 1, lambda _: '片段2')
            self.row('1B', 4, lambda text: text.replace('4-8秒。', '0-4秒。'))
            self.row('1C', 4, lambda text: text.replace('8-12秒。', '4-8秒。'))
            tasks = [
                {'id': '片段1', 'clips': [{'shot': '1A', 'in': 0, 'out': 4}, {'shot': '1C', 'in': 4, 'out': 8}],
                 'references': [], 'reuse_basis': {'reason': '主表演素材前后接续，乙的反应独立生成后插入成片。', 'stage1_evidence': [self.evidence]}},
                {'id': '片段2', 'clips': [{'shot': '1B', 'in': 0, 'out': 4}], 'references': []},
            ]
        else:
            tasks = [{'id': '片段1', 'clips': [{'shot': shot, 'in': index * 4, 'out': index * 4 + 4}
                       for index, shot in enumerate(('1A', '1B', '1C'))], 'references': []}]
        plan = {'version': 1, 'source_stage1_sha256': digest(self.s1), 'tasks': tasks}
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False))
        self.s3.write_text(self.s3.read_text().replace('\n---\n',
            f'\ngeneration_plan: plans/generation.json\ngeneration_plan_sha256: {digest(self.plan_path)}\n---\n', 1))
        return plan

    def write_plan(self, plan):
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False))
        self.field(self.s3, 'generation_plan_sha256', digest(self.plan_path))

    def cli(self, *, expected_error=None, output=None):
        output = output or self.output
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/generation_tasks.py'),
                                 '--stage3', str(self.s3), '--output-dir', str(output)],
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        if expected_error is None:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(expected_error, result.stdout)
            self.assertFalse(output.exists())
        return result

    def add_cards(self):
        self.light = '光影：主光源为桌北侧窗，人物左侧明亮，右侧落入柔暗部。'
        self.background = '背景：当前机位可见墙上的白色挂钟。'
        self.sound = '声音：窗外风声。'
        self.s2.write_text(self.s2.read_text() + '\n### 光影·共同\n\n' + self.light
                          + '\n\n### 背景·挂钟\n\n' + self.background + '\n\n### 场次声音·风声\n\n' + self.sound + '\n')
        self.field(self.s2, 'scene_sound_required', 'yes')
        for shot in ('1A', '1C'):
            self.bindings['shots'][shot]['fixed_cards'] = ['光影·共同', '场次声音·风声']
            self.row(shot, 4, lambda text: text.replace('<br><br>无配乐', '<br><br>' + self.light + '<br><br>无配乐').replace('声音：N/A。', self.sound))
        self.bindings['shots']['1C']['fixed_cards'].append('背景·挂钟')
        self.row('1C', 4, lambda text: text.replace('<br><br>无配乐', '<br><br>' + self.background + '<br><br>无配乐'))
        self.sync()

    def test_legacy_continuous_mode_exports_without_invented_references(self):
        before = {path: path.read_bytes() for path in (self.s1, self.s2, self.s3)}
        self.cli()
        prompt = (self.output / 'task-001.txt').read_text()
        self.assertTrue(all(line in prompt for line in ('第一句。', '第二句。', '第三句。')))
        self.assertIn('8-12秒。', prompt)
        self.assertEqual(prompt.count('无配乐无字幕'), 1)
        refs = json.loads((self.output / 'references.json').read_text())
        self.assertEqual(refs['tasks'], [{'id': '片段1', 'references': []}])
        edit = json.loads((self.output / 'edit-map.json').read_text())
        self.assertEqual([clip['shot'] for clip in edit['clips']], ['1A', '1B', '1C'])
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_plain_height_and_resolved_note_reach_export_without_rewording(self):
        for path in (self.s1, self.s3):
            path.write_text(path.read_text().replace('眼平', '镜头与人物双眼齐高'))
        self.s2.write_text(self.s2.read_text() + '\n无待确认项。\n')
        self.s3.write_text(self.s3.read_text().replace('无配乐无字幕', '无配乐，无字幕。'))
        self.sync()
        before = {p: p.read_bytes() for p in (self.s1, self.s2, self.s3)}
        self.cli()
        prompt = (self.output / 'task-001.txt').read_text()
        self.assertIn('镜头与人物双眼齐高', prompt)
        self.assertEqual(prompt.count('无配乐无字幕'), 1)
        self.assertNotIn('无配乐，无字幕。', prompt)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.s2.write_text(self.s2.read_text() + '\n- 🔴 主光方向尚未确定，不能制作。\n')
        self.sync()
        self.cli(expected_error='明确的🔴阻塞', output=self.root / 'blocked-export')

    def test_export_reuses_one_authority_check_and_keeps_source_protection(self):
        import validate_v2_prompts as validation
        with patch.object(validation, 'stage3_authority', wraps=validation.stage3_authority) as checked:
            generation.compile_delivery(self.s3, self.output)
        self.assertEqual(checked.call_count, 1)
        self.s1.write_text(self.s1.read_text() + '\n新的尚未核对来源变更。\n')
        self.cli(expected_error='摘要', output=self.root / 'stale-export')

    def test_equivalent_movement_exports_unchanged_with_visible_review_lead(self):
        self.row('1A', 4, lambda text: text.replace('固定镜头', '三脚架静止拍摄'))
        before = {p: p.read_bytes() for p in (self.s1, self.s2, self.s3)}
        validation = subprocess.run([sys.executable, str(ROOT / 'scripts/validate_v2_prompts.py'),
                                    '--stage3', str(self.s3)],
                                   capture_output=True, text=True, cwd=self.root, timeout=20)
        result = self.cli()
        self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
        for reply in (validation, result):
            self.assertIn('WARNING: 镜头1A', reply.stderr)
            self.assertIn('非阻塞', reply.stderr)
            self.assertIn('固定镜头', reply.stderr)
            self.assertIn('三脚架静止拍摄', reply.stderr)
            self.assertIn('现有内容自查', reply.stderr)
        task = (self.output / 'task-001.txt').read_text()
        self.assertIn('三脚架静止拍摄', task)
        self.assertNotIn('WARNING', task)
        self.assertNotIn('词集合', task)
        edit = json.loads((self.output / 'edit-map.json').read_text())
        warning, = edit['content_warnings']
        self.assertEqual(warning['shot'], '1A')
        self.assertIn('三脚架静止拍摄', warning['prompt_camera'])
        self.assertIn('固定镜头', warning['source_camera'])
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_changed_movement_surfaces_for_existing_content_review(self):
        self.row('1A', 4, lambda text: text.replace('固定镜头', '缓慢推近'))
        result = self.cli()
        self.assertIn('WARNING: 镜头1A', result.stderr)
        self.assertIn('实际运动变化或遗漏则修正', result.stderr)
        warning, = json.loads((self.output / 'edit-map.json').read_text())['content_warnings']
        self.assertIn('固定镜头', warning['source_camera'])
        self.assertIn('缓慢推近', warning['prompt_camera'])
        self.assertIn('缓慢推近', (self.output / 'task-001.txt').read_text())

    def test_movement_warning_does_not_bypass_hard_delivery_errors(self):
        self.row('1A', 4, lambda text: text.replace('固定镜头', '三脚架静止拍摄'))
        original = self.s3.read_text()
        for label, column, old, new, error in (
            ('focal', 4, '50mm', '85mm', '焦段'),
            ('dialogue', 4, '第一句。', '未经批准的新台词。', '文本不在对应正文'),
            ('time', 4, '0-4秒。', '0-5秒。', '累计时间'),
            ('verbatim', 3, '固定镜头', '三脚架静止拍摄', '逐字合并'),
        ):
            with self.subTest(label=label):
                self.s3.write_text(original)
                self.row('1A', column, lambda text: text.replace(old, new))
                reply = self.cli(expected_error=error, output=self.root / label)
                self.assertIn('WARNING: 镜头1A', reply.stderr)
        self.s3.write_text(original)
        self.s1.write_text(self.s1.read_text() + '\n未同步的上游变化。\n')
        self.cli(expected_error='摘要', output=self.root / 'stale')

    def test_authored_motion_and_global_restrictions_reach_the_actual_task(self):
        for path in (self.s1, self.s3):
            path.write_text(path.read_text().replace('固定镜头', '三脚架静止拍摄'))
        self.s3.write_text(self.s3.read_text().replace('无配乐无字幕', '不添加配乐和字幕。'))
        self.sync()
        self.cli()
        prompt = (self.output / 'task-001.txt').read_text()
        self.assertIn('三脚架静止拍摄', prompt)
        self.assertIn('不添加配乐和字幕。', prompt)
        self.assertTrue(prompt.rstrip().endswith('无配乐无字幕'))
        self.assertEqual(prompt.count('无配乐无字幕'), 1)

    def test_global_restrictions_are_emitted_even_without_per_shot_tail(self):
        self.s3.write_text(self.s3.read_text().replace('<br><br>无配乐无字幕', ''))
        self.cli()
        prompt = (self.output / 'task-001.txt').read_text()
        self.assertEqual(prompt.count('无配乐无字幕'), 1)
        self.assertTrue(prompt.rstrip().endswith('无配乐无字幕'))

    def test_reuse_exports_full_tasks_shared_conditions_and_exact_edit_order(self):
        self.add_cards()
        plan = self.plan()
        ref = self.plan_path.parent / 'character.svg'
        ref.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        plan['tasks'][0]['references'] = [{'label': '@Image 1', 'path': 'character.svg', 'sha256': digest(ref), 'use': '甲的角色身份'}]
        self.write_plan(plan)
        before = self.s3.read_bytes()
        self.cli()
        first = (self.output / 'task-001.txt').read_text()
        second = (self.output / 'task-002.txt').read_text()
        self.assertEqual(first.count(self.light), 1)
        self.assertLess(first.index(self.light), first.index('0-4秒。'))
        self.assertEqual(first.count(self.background), 1)
        self.assertGreater(first.index(self.background), first.index('4-8秒。'))
        self.assertEqual(first.count(self.sound), 2)  # Sound remains at each original time.
        self.assertLess(first.index('第一句。'), first.index('第三句。'))
        self.assertNotIn('第二句。', first)
        self.assertIn('第二句。', second)
        self.assertEqual(first.count('无配乐无字幕'), 1)
        self.assertIn('@Image 1：甲的角色身份', first)
        refs = json.loads((self.output / 'references.json').read_text())
        self.assertEqual(refs['tasks'][0]['references'][0]['path'], str(ref.resolve()))
        self.assertEqual(refs['tasks'][0]['references'][0]['sha256'], digest(ref))
        edit = json.loads((self.output / 'edit-map.json').read_text())
        self.assertEqual(edit['clips'], [
            {'shot': '1A', 'task': '片段1', 'in': 0, 'out': 4, 'timeline_in': 0, 'timeline_out': 4},
            {'shot': '1B', 'task': '片段2', 'in': 0, 'out': 4, 'timeline_in': 4, 'timeline_out': 8},
            {'shot': '1C', 'task': '片段1', 'in': 4, 'out': 8, 'timeline_in': 8, 'timeline_out': 12},
        ])
        self.assertEqual(self.s3.read_bytes(), before)

    def test_same_text_single_shot_exceptions_are_not_promoted_to_shared_cards(self):
        light = '光影：主光源为甲左侧窗，左侧明亮，右侧落入暗部。'
        for shot in ('1A', '1C'):
            self.bindings['shots'][shot]['exceptions'] = {'光影': {'text': light, 'basis': '当前机位单独描述。', 'stage1_evidence': '甲站在画面中央'}}
            self.row(shot, 4, lambda text: text.replace('<br><br>无配乐', '<br><br>' + light + '<br><br>无配乐'))
        self.sync()
        self.plan()
        self.cli()
        prompt = (self.output / 'task-001.txt').read_text()
        self.assertEqual(prompt.count(light), 2)
        self.assertNotIn('全任务固定条件：', prompt)

    def test_reuse_without_a_plan_remains_rejected_by_public_cli(self):
        self.plan()
        self.s3.write_text('\n'.join(line for line in self.s3.read_text().splitlines() if not line.startswith('generation_plan')) + '\n')
        self.cli(expected_error='不得倒退或跳号')

    def test_plan_declarations_must_be_paired_and_content_digest_current(self):
        self.plan()
        original = self.s3.read_text()
        self.s3.write_text('\n'.join(line for line in original.splitlines() if not line.startswith('generation_plan_sha256:')) + '\n')
        self.cli(expected_error='必须成对声明')
        self.s3.write_text(original)
        self.plan_path.write_text(self.plan_path.read_text() + '\n')
        self.cli(expected_error='generation_plan摘要')

    def test_plan_source_binding_and_validated_upstream_cannot_be_skipped(self):
        plan = self.plan()
        plan['source_stage1_sha256'] = '0' * 64
        self.write_plan(plan)
        self.cli(expected_error='generation_plan 的 Stage 1摘要')
        plan['source_stage1_sha256'] = digest(self.s1)
        self.write_plan(plan)
        self.field(self.s2, 'status', 'draft')
        self.sync()
        self.cli(expected_error='尚未 approved')

    def test_invalid_clips_and_unsubstantiated_reuse_fail_before_writing(self):
        original = self.plan()
        cases = []
        plan = copy.deepcopy(original); plan['tasks'][0].pop('reuse_basis'); cases.append((plan, 'reuse_basis 理由'))
        plan = copy.deepcopy(original); plan['tasks'][0]['reuse_basis']['stage1_evidence'] = ['原文中不存在的连接']; cases.append((plan, '真实原文'))
        plan = copy.deepcopy(original); plan['tasks'][0]['clips'].pop(); cases.append((plan, '完整且仅映射一次'))
        plan = copy.deepcopy(original); plan['tasks'][1]['clips'] = [{'shot': '1A', 'in': 0, 'out': 4}]; cases.append((plan, '镜头重复映射'))
        plan = copy.deepcopy(original); plan['tasks'][0]['clips'].reverse(); cases.append((plan, '原相对顺序'))
        plan = copy.deepcopy(original); plan['tasks'][0]['clips'][1]['in'] = 5; cases.append((plan, '不得重叠或留空洞'))
        plan = copy.deepcopy(original); plan['tasks'][0]['clips'][1]['in'] = 3; cases.append((plan, '不得重叠或留空洞'))
        plan = copy.deepcopy(original); plan['tasks'][0]['clips'][1]['out'] = 9; cases.append((plan, '素材时长与 Stage 1'))
        plan = copy.deepcopy(original); plan['tasks'][0]['clips'][1]['out'] = 31; cases.append((plan, '超过30秒'))
        plan = copy.deepcopy(original); plan['tasks'][1]['id'] = '片段3'; cases.append((plan, '连续编号'))
        plan = copy.deepcopy(original); plan['tasks'][0]['clips'][0]['in'] = True; cases.append((plan, 'JSON数值'))
        for plan, expected in cases:
            with self.subTest(error=expected):
                self.write_plan(plan)
                self.cli(expected_error=expected)

    def test_task_clock_is_distinct_from_edit_clock(self):
        self.plan()
        self.row('1C', 4, lambda text: text.replace('4-8秒。', '8-12秒。'))
        self.cli(expected_error='提示词累计时间')

    def test_task_self_containment_is_not_certified_by_phrase_scanning(self):
        self.plan()
        self.row('1C', 4, lambda text: text.replace('动作/表情：', '动作/表情：继续上一镜的同一段表演，'))
        self.assertEqual(check3(self.s3), [])
        self.cli()
        self.row('1B', 4, lambda text: text.replace('动作/表情：', '动作/表情：继续上一镜的表演，'))
        self.cli(output=self.root / 'content-review-required')  # 自足性不能由一个短语判定

    def test_reference_files_paths_labels_and_digests_are_validated(self):
        original = self.plan()
        ref = self.plan_path.parent / 'identity.svg'
        ref.write_text('<svg/>')
        (self.root / 'identity.svg').write_text('同名错误文件')
        reference = {'label': '@Image 1', 'path': 'identity.svg', 'sha256': digest(ref), 'use': '甲的身份'}
        original['tasks'][0]['references'] = [reference]
        self.write_plan(original)
        self.assertEqual(check3(self.s3), [])
        duplicate = copy.deepcopy(original)
        duplicate['tasks'][0]['references'].append(reference)
        self.write_plan(duplicate)
        self.cli(expected_error='参考标签重复')
        self.write_plan(original)
        ref.write_text('<svg>changed</svg>')
        self.cli(expected_error='参考素材 @Image 1摘要')
        ref.unlink()
        self.cli(expected_error='文件不存在')

    def test_undeclared_reference_is_not_invented_by_legacy_or_planned_export(self):
        self.row('1A', 4, lambda text: text.replace('画面：', '画面：参考@Image 1，'))
        self.cli(expected_error='须先在绑定的 generation_plan 声明素材')
        self.plan()
        self.cli(expected_error='未声明的素材标签')

    def test_chinese_reference_labels_are_bound_exported_and_unique(self):
        plan = self.plan()
        ref = self.plan_path.parent / 'identity.svg'
        ref.write_text('<svg/>')
        labels = ['@图片1', '@视频1', '@角色甲']
        plan['tasks'][0]['references'] = [
            {'label': label, 'path': 'identity.svg', 'sha256': digest(ref), 'use': '甲的参考身份'}
            for label in labels
        ]
        self.row('1A', 4, lambda text: text.replace('画面：', '画面：参考' + '、'.join(labels) + '，'))
        self.write_plan(plan)
        self.cli()
        refs = json.loads((self.output / 'references.json').read_text())
        self.assertEqual([ref['label'] for ref in refs['tasks'][0]['references']], labels)
        prompt = (self.output / 'task-001.txt').read_text()
        self.assertTrue(all(label + '：甲的参考身份' in prompt for label in labels))
        plan['tasks'][0]['references'].append(plan['tasks'][0]['references'][0])
        self.write_plan(plan)
        self.cli(expected_error='参考标签重复：@图片1', output=self.root / 'duplicate-chinese')
        plan['tasks'][0]['references'].pop()
        plan['tasks'][0]['references'][0]['label'] = '@图片1 @角色甲'
        self.write_plan(plan)
        self.cli(expected_error='单个明确的 @ 引用标签', output=self.root / 'multiple-labels')

    def test_undeclared_chinese_labels_cannot_bypass_reference_binding(self):
        self.plan()
        original = self.s3.read_text()
        for label in ('@图片1', '@视频1', '@角色甲'):
            with self.subTest(label=label):
                self.s3.write_text(original)
                self.row('1A', 4, lambda text: text.replace('画面：', f'画面：参考{label}，'))
                self.cli(expected_error='未声明的素材标签：' + label)

    def test_numbered_references_touch_chinese_without_truncation_or_prefix_aliasing(self):
        plan = self.plan(reuse=False)
        ref = self.root / 'identity.svg'
        ref.write_text('<svg/>')
        plan['tasks'][0]['references'] = [
            {'label': label, 'path': '../identity.svg', 'sha256': digest(ref), 'use': '场景材质'}
            for label in ('@Image 1', '@Image 10')]
        self.write_plan(plan)
        self.row('1A', 4, lambda text: text.replace('画面：', '画面：参考@Image 1与@Image 10中的布景。'))
        self.cli()
        exported = (self.output / 'task-001.txt').read_text()
        self.assertIn('参考@Image 1与@Image 10中的布景。', exported)
        self.row('1A', 4, lambda text: text.replace('@Image 10中的', '@Image 100中的'))
        self.cli(expected_error='未声明的素材标签：@Image 100', output=self.root / 'unknown-number')

    def test_declared_custom_references_touch_chinese_and_longer_names_win(self):
        plan = self.plan(reuse=False)
        ref = self.root / 'identity.svg'
        ref.write_text('<svg/>')
        labels = ['@角色甲', '@角色甲乙', '@图片1', '@视频1']
        plan['tasks'][0]['references'] = [
            {'label': label, 'path': '../identity.svg', 'sha256': digest(ref), 'use': '对应参考'}
            for label in labels]
        self.write_plan(plan)
        expression = '参考@角色甲乙的外观、@角色甲的动作及@图片1与@视频1。'
        self.row('1A', 4, lambda text: text.replace('画面：', '画面：' + expression))
        self.cli()
        self.assertIn(expression, (self.output / 'task-001.txt').read_text())
        self.assertEqual(set(generation._reference_pattern(labels).findall(expression)), set(labels))
        self.row('1A', 4, lambda text: text.replace('@图片1与', '@图片10与'))
        self.cli(expected_error='未声明的素材标签', output=self.root / 'unknown-chinese-number')

    def test_reference_digests_are_shared_within_plan_and_multiline_use_is_preserved(self):
        plan = self.plan()
        ref = self.root / 'identity.svg'
        ref.write_text('<svg/>')
        purpose = '只参考材质。\n不改变已定布景。'
        for task in plan['tasks']:
            task['references'] = [
                {'label': '@Image 1', 'path': '../identity.svg', 'sha256': digest(ref), 'use': purpose}]
        self.write_plan(plan)
        with patch.object(generation, 'digest', wraps=generation.digest) as reads:
            generation.compile_delivery(self.s3, self.output)
        image_reads = [call for call in reads.call_args_list if call.args[0].resolve() == ref.resolve()]
        self.assertEqual(len(image_reads), 1)  # Only plan-reference hashing; final snapshot still verifies files.
        for number in (1, 2):
            self.assertIn('@Image 1：' + purpose, (self.output / f'task-{number:03}.txt').read_text())
        refs = json.loads((self.output / 'references.json').read_text())
        self.assertTrue(all(task['references'][0]['use'] == purpose for task in refs['tasks']))
        # No cache survives into a later export or hides a changed file.
        ref.write_text('<svg>changed</svg>')
        self.cli(expected_error='参考素材 @Image 1摘要', output=self.root / 'changed-image')

    def test_email_addresses_are_not_misread_as_reference_labels(self):
        self.row('1A', 4, lambda text: text.replace('画面：',
            '画面：联系标牌显示alice+film@example.com、crew@片段.example、作者@示例.公司和word@角色甲，'))
        self.cli()
        self.plan(reuse=False)
        self.cli(output=self.root / 'planned-email')

    def test_reuse_task_cannot_span_different_stage1_scenes(self):
        text = self.s1.read_text()
        last = next(line for line in text.splitlines() if line.startswith('| 1 | 1C |'))
        text = text.replace(last + '\n', '').replace('场次时长：12s', '场次时长：8s')
        text += '\n## 场次 2\n\n- 场景图：scene.svg\n- 基础全景机位与主轴线：摄影机在桌南侧。\n- far-side key基准：北侧窗。\n- 场次时长：4s\n\n### 成片主序列\n\n| 场次 | 镜号 | 时长 | 摄影机 | 画面内容 | 音效 | 剪辑衔接 |\n| --- | --- | --- | --- | --- | --- | --- |\n'
        text += last.replace('| 1 | 1C |', '| 2 | 2A |') + '\n'
        self.s1.write_text(text)
        self.field(self.s1, 'scene_images_sha256', json.dumps({'1': digest(self.root / 'scene.svg'), '2': digest(self.root / 'scene.svg')}))
        self.extra_stage2 = self.root / 'stage2b.md'
        self.extra_stage2.write_text(self.s2.read_text().replace('\nscene: 1\n', '\nscene: 2\n'))
        self.field(self.s3, 'source_stage2', 'stage2.md,stage2b.md')
        plan = self.plan()
        self.s3.write_text(self.s3.read_text().replace('| 1C |', '| 2A |'))
        dialogue = self.root / 'dialogue.json'
        record = json.loads(dialogue.read_text())
        record['stage1_speech']['2A'] = record['stage1_speech'].pop('1C')
        dialogue.write_text(json.dumps(record, ensure_ascii=False))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(dialogue))
        self.bindings['shots']['2A'] = self.bindings['shots'].pop('1C')
        self.sync()
        plan['source_stage1_sha256'] = digest(self.s1)
        plan['tasks'][0]['clips'][1]['shot'] = '2A'
        self.write_plan(plan)
        self.assertEqual(check1(self.s1), [])
        self.cli(expected_error='不得跨 Stage 1 场次')

    def test_existing_output_directory_is_not_overwritten(self):
        self.output.mkdir()
        protected = self.output / 'task-001.txt'
        protected.write_text('保留现有文件')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/generation_tasks.py'), '--stage3', str(self.s3),
                                 '--output-dir', str(self.output)], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('新的导出目录', result.stdout)
        self.assertEqual(protected.read_text(), '保留现有文件')

    def bound_input_files(self):
        """Declare real files for every input kind followed by the exporter."""
        director = fixtures.DeliveryChecks.bind_plan(self)
        facts = self.root / 'scene-facts.md'
        facts.write_text('桌高0.75米。')
        config = self.root / 'config'
        config.mkdir()
        extras = []
        for role in ('character', 'prop', 'production', 'reference'):
            path = config / (role + '.md')
            path.write_text(role + '实际制作条件')
            extras.append(path)
        manifest = config / 'inputs.json'
        manifest.write_text(json.dumps({'inputs': [{'path': path.name, 'sha256': digest(path)} for path in extras]}, ensure_ascii=False))
        self.s1.write_text(self.s1.read_text().replace('\n---\n',
            f'\nscene_facts: scene-facts.md\nscene_facts_sha256: {digest(facts)}'
            f'\ninput_manifest: config/inputs.json\ninput_manifest_sha256: {digest(manifest)}\n---\n', 1))
        self.sync()
        plan = self.plan()
        ref = self.plan_path.parent / 'identity.svg'
        ref.write_text('<svg/>')
        plan['tasks'][0]['references'] = [{'label': '@图片1', 'path': ref.name, 'sha256': digest(ref), 'use': '甲的身份'}]
        self.write_plan(plan)
        return [self.s3, self.s1, self.s2, self.root / 'source.md', self.root / 'scene.svg',
                self.root / 'notes.md', self.root / 'dialogue.json', self.bindings_path,
                director, facts, manifest, *extras, self.plan_path, ref]

    def concurrent_export(self, edit, *, after_final_check=False):
        """A scheduling barrier only: another thread performs ordinary edits."""
        ready, finished = threading.Event(), threading.Event()
        writer_errors = []

        def writer():
            try:
                if not ready.wait(10):
                    raise RuntimeError('编译未到达并发回归同步点')
                edit()
            except BaseException as exc:
                writer_errors.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=writer)
        thread.start()
        reached = False

        def barrier():
            nonlocal reached
            if not reached:
                reached = True
                ready.set()
                if not finished.wait(10):
                    raise RuntimeError('并发文件编辑未完成')

        if after_final_check:
            original = generation._InputSnapshot.verify_unchanged

            def paused(snapshot):
                result = original(snapshot)
                barrier()
                return result

            owner, name = generation._InputSnapshot, 'verify_unchanged'
        else:
            original = generation._common_visuals

            def paused(*args):
                result = original(*args)
                barrier()
                return result

            owner, name = generation, '_common_visuals'
        try:
            with patch.object(owner, name, paused):
                return generation.compile_delivery(self.s3, self.output)
        finally:
            ready.set()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertFalse(writer_errors, writer_errors)

    def test_concurrent_declared_input_edits_abort_before_any_package_is_written(self):
        targets = self.bound_input_files()
        self.assertEqual(check3(self.s3), [])
        for target in targets:
            original = target.read_bytes()
            with self.subTest(target=target.name):
                try:
                    with self.assertRaisesRegex(ValueError, '导出期间输入已变化'):
                        self.concurrent_export(lambda: target.write_bytes(original + b'\nchanged during compile'))
                    self.assertFalse(self.output.exists())
                    self.assertNotEqual(target.read_bytes(), original)
                finally:
                    target.write_bytes(original)

    def test_an_edit_then_restore_still_invalidates_the_in_progress_export(self):
        original = self.s3.read_bytes()

        def edit_and_restore():
            self.s3.write_bytes(original.replace('第一句。'.encode(), '已改对白。'.encode()))
            self.s3.write_bytes(original)

        with self.assertRaisesRegex(ValueError, '导出期间输入已变化'):
            self.concurrent_export(edit_and_restore)
        self.assertEqual(self.s3.read_bytes(), original)
        self.assertFalse(self.output.exists())

    def test_edit_after_the_final_check_cannot_relabel_old_text_with_a_new_digest(self):
        initial = self.s3.read_bytes()
        initial_hash = digest(self.s3)
        self.concurrent_export(lambda: self.s3.write_bytes(initial.replace('第一句。'.encode(), '已改对白。'.encode())),
                               after_final_check=True)
        exported = (self.output / 'task-001.txt').read_text()
        provenance = json.loads((self.output / 'edit-map.json').read_text())['sources']
        self.assertIn('第一句。', exported)
        self.assertNotIn('已改对白。', exported)
        self.assertEqual(provenance['stage3']['sha256'], initial_hash)
        self.assertNotEqual(provenance['stage3']['sha256'], digest(self.s3))
        self.assertTrue(check3(self.s3))

    def test_successful_export_records_the_complete_declared_input_snapshot(self):
        targets = self.bound_input_files()
        expected = {str(path.resolve()): digest(path) for path in targets}
        self.cli()
        provenance = json.loads((self.output / 'edit-map.json').read_text())['sources']
        actual = {entry['path']: entry['sha256'] for entry in provenance['inputs']}
        self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
