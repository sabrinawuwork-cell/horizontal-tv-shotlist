from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from v2_delivery_checks import digest, contains_sound_unit
from validate_v2_shotlist import validate_file as check1, speaker_line_dialogue
from validate_v2_stage2 import validate_file as check2
from validate_v2_prompts import validate_stage3 as check3, checked_stage3


def markdown(meta, body):
    return '---\n' + ''.join(f'{key}: {value}\n' for key, value in meta.items()) + '---\n\n' + body


class DeliveryChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.s1, self.s2, self.s3 = [self.root / f'stage{i}.md' for i in (1, 2, 3)]
        (self.root / 'source.md').write_text('甲站在桌西说：“第一句。”乙站在桌东说：“第二句。”')
        (self.root / 'scene.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="100" height="100"/></svg>')
        (self.root / 'notes.md').write_text('甲在桌西、乙在桌东；南侧摄影机。1A看甲、1B看乙；身体不换位。')
        (self.root / 'dialogue.json').write_text(json.dumps({'dialogue': ['第一句。', '第二句。'], 'source_speech': ['第一句。', '第二句。'], 'stage1_speech': {'1A': ['第一句。'], '1B': ['第二句。']}}))
        self.meta1 = dict(artifact='horizontal-tv-shotlist', project='fixture', episode='E1', version='1', status='approved',
            source='source.md', source_sha256=digest(self.root / 'source.md'), aspect_ratio='16:9', shooting_language='zh',
            production_profile='ai-video', total_duration='8s', blocking_issues='none', long_shot_authorizations='none',
            active_cast_authorizations='none', scene_image_authorizations='none', dialogue_manifest='dialogue.json',
            dialogue_manifest_sha256=digest(self.root / 'dialogue.json'), continuity_notes='notes.md',
            scene_images_sha256=json.dumps({'1': digest(self.root / 'scene.svg')}),
            continuity_notes_sha256=digest(self.root / 'notes.md'))
        self.shots = [('1A', '甲', '第一句。'), ('1B', '乙', '第二句。')]
        self.cameras = {shot: f'{name}的中景，50mm，机位在{name}右前方，眼平固定镜头。' for shot, name, _ in self.shots}
        self.pictures = {shot: f'{name}站在画面中央说：“{line}”' for shot, name, line in self.shots}
        body = '## 场次 1\n\n- 场景图：scene.svg\n- 基础全景机位与主轴线：摄影机在桌南侧。\n- far-side key基准：北侧窗。\n- 场次时长：8s\n\n### 成片主序列\n\n| 场次 | 镜号 | 时长 | 摄影机 | 画面内容 | 音效 | 剪辑衔接 |\n| --- | --- | --- | --- | --- | --- | --- |\n'
        for shot, _, _ in self.shots:
            body += f'| 1 | {shot} | 4s | {self.cameras[shot]} | {self.pictures[shot]} | N/A | 台词结束后切镜。 |\n'
        self.s1.write_text(markdown(self.meta1, body))
        self.meta2 = dict(artifact='horizontal-tv-stage2', project='fixture', episode='E1', version='1', status='approved',
            source_stage1='stage1.md', scene='1', scene_image='scene.svg', stage1_sha256=digest(self.s1), speaking_characters='甲,乙',
            voice_prompt_required='no', environment_required='no', extras_required='no', scene_sound_required='no')
        self.s2.write_text(markdown(self.meta2, '# 场次固定条件\n\n本场无须建立固定类别。\n'))
        self.bindings = {'source_stage1_sha256': digest(self.s1), 'shots': {shot: {'fixed_cards': [], 'speech': [line]} for shot, _, line in self.shots}}
        self.bindings_path = self.root / 'bindings.json'
        self.bindings_path.write_text(json.dumps(self.bindings))
        meta3 = dict(artifact='horizontal-tv-stage3-v2', project='fixture', episode='E1', version='1', status='approved',
            source_stage1='stage1.md', source_stage2='stage2.md', stage1_sha256=digest(self.s1), stage2_sha256=digest(self.s2),
            model='seedance-2.5', segment_hard_limit='30s', dialogue_sensitive_authorizations='none',
            stage3_bindings='bindings.json', stage3_bindings_sha256=digest(self.bindings_path))
        body3 = '| 镜号 | 片段 | 时长 | 分镜内容 | Seedance提示词 |\n| --- | --- | --- | --- | --- |\n'
        for index, (shot, name, line) in enumerate(self.shots):
            prompt = (f'{index*4}-{index*4+4}秒。50mm，{self.cameras[shot].replace("50mm，", "")}'
                f'<br><br>画面：{name}站在画面中央。<br><br>动作/表情：{name}说：“{line}”'
                f'镜头最终停在{name}站在画面中央且身体停止运动的状态。<br><br>声音：N/A。<br><br>无配乐无字幕')
            story = f'摄影机：{self.cameras[shot]}<br><br>画面内容：{self.pictures[shot]}<br><br>音效：N/A'
            body3 += f'| {shot} | 片段1 | 4s | {story} | {prompt} |\n'
        self.s3.write_text(markdown(meta3, body3))

    def field(self, path, key, value):
        text = path.read_text()
        text, count = re.subn(rf'(?m)^{key}:.*$', f'{key}: {value}', text)
        self.assertEqual(count, 1)
        path.write_text(text)

    def row(self, shot, column, change):
        lines = self.s3.read_text().splitlines()
        for index, line in enumerate(lines):
            if line.startswith(f'| {shot} |'):
                cells = [cell.strip() for cell in line.strip('|').split('|')]
                cells[column] = change(cells[column])
                lines[index] = '| ' + ' | '.join(cells) + ' |'
        self.s3.write_text('\n'.join(lines) + '\n')

    def rejects(self, check, path, expected):
        errors = check(path)
        self.assertTrue(any(expected in e for e in errors), errors)

    def test_valid_three_stage_delivery(self):
        for check, path in ((check1, self.s1), (check2, self.s2), (check3, self.s3)):
            self.assertEqual(check(path), [])

    def test_protected_dialogue_can_name_a_format_concept(self):
        phrase = '别越过轴线。'
        source = self.root / 'source.md'
        source.write_text(source.read_text().replace('第一句。', phrase))
        manifest = self.root / 'dialogue.json'
        manifest.write_text(json.dumps({'dialogue': [phrase, '第二句。'], 'source_speech': [phrase, '第二句。'], 'stage1_speech': {'1A': [phrase], '1B': ['第二句。']}}, ensure_ascii=False))
        self.s1.write_text(self.s1.read_text().replace('第一句。', phrase))
        self.field(self.s1, 'source_sha256', digest(source))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(manifest))
        self.assertEqual(check1(self.s1), [])

        self.s1.write_text(self.s1.read_text().replace('甲站在画面中央', '轴线：甲站在画面中央', 1))
        self.assertEqual(check1(self.s1), [])  # 字词是否为不当分析由内容复核判断

    def test_camera_can_cut_out_while_still_moving(self):
        text = self.s1.read_text()
        old = self.pictures['1A']
        new = ('起幅为甲的中景。甲开始转头时摄影机沿视轴缓慢前推，'
               '甲保持在画面中央说：“第一句。”台词结束时画幅收至近景，'
               '摄影机仍以原速度前推，切出前头部与双肩完整入画。')
        text = text.replace(old, new)
        camera = self.cameras['1A']
        text = text.replace(camera, camera.replace('固定镜头', '缓慢推近'), 1)
        self.s1.write_text(text)
        self.assertEqual(check1(self.s1), [])

    def test_motion_cut_and_offscreen_target_reach_actual_task_export(self):
        camera = self.cameras['1A'].replace('固定镜头', '缓慢推近')
        picture = ('甲站在画面中央，看向画外的乙。甲开口时摄影机沿视轴缓慢前推，'
                   '甲说：“第一句。”话音结束时收至近景，摄影机仍在前推，'
                   '切出前头部与双肩完整入画，乙不入画。')
        self.s1.write_text(self.s1.read_text().replace(self.cameras['1A'], camera)
                           .replace(self.pictures['1A'], picture))
        self.row('1A', 3, lambda _: f'摄影机：{camera}<br><br>画面内容：{picture}<br><br>音效：N/A')
        prompt = (f'0-4秒。{camera}<br><br>画面：甲站在画面中央，乙不入画。'
                  f'<br><br>动作/表情：{picture}<br><br>声音：N/A。<br><br>无配乐无字幕')
        self.row('1A', 4, lambda _: prompt)
        self.field(self.s2, 'stage1_sha256', digest(self.s1))
        self.bindings['source_stage1_sha256'] = digest(self.s1)
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage1_sha256', digest(self.s1))
        self.field(self.s3, 'stage2_sha256', digest(self.s2))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        for check, path in ((check1, self.s1), (check2, self.s2), (check3, self.s3)):
            self.assertEqual(check(path), [])
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/generation_tasks.py'),
                                 '--stage3', str(self.s3), '--output-dir', str(self.root / 'tasks')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        exported = (self.root / 'tasks/task-001.txt').read_text()
        self.assertIn('看向画外的乙', exported)
        self.assertIn('摄影机仍在前推', exported)

    def test_scene_display_titles_preserve_the_scene_identifier(self):
        original = self.s1.read_text()
        for heading in ('# 场次 1｜社区听证室', '## 场次1：社区听证室', '### 场次 1'):
            with self.subTest(heading=heading):
                self.s1.write_text(original.replace('## 场次 1', heading))
                self.assertEqual(check1(self.s1), [])

    def test_source_revision_invalidates_stage1(self):
        (self.root / 'source.md').write_text('新版源剧本')
        self.rejects(check1, self.s1, '剧本摘要')

    def bind_plan(self, *, status='approved'):
        plan = self.root / 'director-plan.md'
        plan.write_text(markdown(dict(artifact='horizontal-tv-director-plan', source='source.md',
            source_sha256=digest(self.root / 'source.md'), scene_images_sha256=json.dumps({'1': digest(self.root / 'scene.svg')}), status=status),
            '甲乙在桌两端完成问答；从南侧观看，先看甲，再看乙。人物不换位，镜头切换保持同一关系。'))
        text = self.s1.read_text().replace('\n---\n', f'\ndirector_plan: director-plan.md\ndirector_plan_sha256: {digest(plan)}\n---\n', 1)
        self.s1.write_text(text)
        return plan

    def test_new_generation_requires_plan_binding_but_external_input_remains_supported(self):
        self.assertTrue(any('前置导演方案' in e for e in check1(self.s1, require_plan=True)))
        self.assertEqual(check1(self.s1), [])
        self.bind_plan()
        self.assertEqual(check1(self.s1, require_plan=True), [])

    def test_changed_plan_invalidates_shotlist_binding(self):
        plan = self.bind_plan()
        plan.write_text(plan.read_text() + '\n改为从北侧观看。')
        self.rejects(check1, self.s1, '导演方案摘要')

    def test_plan_must_reference_same_source_and_confirmation(self):
        plan = self.bind_plan(status='draft')
        self.rejects(check1, self.s1, '同时确认')
        self.field(plan, 'status', 'approved')
        (self.root / 'other-source.md').write_text('不同的剧本')
        self.field(plan, 'source', 'other-source.md')
        self.field(self.s1, 'director_plan_sha256', digest(plan))
        self.rejects(check1, self.s1, '不是同一文件')

    def test_body_lighting_does_not_require_a_background(self):
        from validate_v2_prompts import light_errors
        self.assertEqual(light_errors('光影：主光源为角色左侧窗，4000K，左侧250 lux亮面，右侧80 lux暗部。'), [])
        self.assertEqual(light_errors('光影：主光源为人物正前方柔光灯，4000K，人物正面250 lux亮面，背面80 lux暗部。'), [])
        self.assertEqual(light_errors('光影：主光源为道具正上方台灯，4000K，顶部250 lux亮面，底面80 lux暗部。'), [])

    def test_authorized_imageless_draft_remains_a_draft(self):
        plan = self.bind_plan(status='draft')
        self.field(self.s1, 'status', 'draft')
        self.field(self.s1, 'scene_image_authorizations', '1')
        self.field(self.s1, 'scene_images_sha256', '{}')
        self.field(self.s1, 'blocking_issues', '场景图待补，只有文字草案')
        self.s1.write_text(self.s1.read_text().replace('场景图：scene.svg', '场景图：待补（临时文字草案）'))
        self.field(plan, 'scene_images_sha256', '{}')
        self.field(self.s1, 'director_plan_sha256', digest(plan))
        self.assertEqual(check1(self.s1, require_plan=True), [])
        self.field(self.s1, 'status', 'approved')
        self.assertTrue(check1(self.s1, require_plan=True))

    def test_static_prop_result_does_not_require_a_pose_keyword(self):
        from validate_v2_prompts import prompt_errors
        prompt = '0-4秒。100mm，纸页特写，贴地俯拍固定。\n\n画面：纸页在登记簿上。\n\n动作/表情：纸页平放在登记簿上，交接结束。\n\n声音：N/A。\n\n无配乐无字幕'
        self.assertEqual(prompt_errors(prompt, set()), [])

    def test_light_and_view_dependent_background_are_independent_cards(self):
        light = '光影：主光源为甲左侧窗，4000K，左侧250 lux亮面，右侧80 lux暗部。'
        background = '背景：当前机位可见参考图中的墙面。'
        self.s2.write_text(self.s2.read_text() + '\n### 光影·甲\n\n' + light + '\n\n### 背景·墙\n\n' + background + '\n')
        self.field(self.s3, 'stage2_sha256', digest(self.s2))
        self.bindings['shots']['1A']['fixed_cards'] = ['光影·甲', '背景·墙']
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.row('1A', 4, lambda s: s.replace('<br><br>无配乐', '<br><br>' + light + '<br><br>' + background + '<br><br>无配乐'))
        self.assertEqual(check3(self.s3), [])

    def test_transition_intent_can_be_compiled_in_the_existing_camera_block(self):
        self.row('1B', 4, lambda s: s.replace('4-8秒。', '4-8秒。硬切到当前镜头。'))
        self.assertEqual(check3(self.s3), [])

    def test_source_image_content_is_pinned(self):
        (self.root / 'scene.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.rejects(check3, self.s3, '场景图摘要')

    def test_empty_manifest_cannot_hide_missing_source_dialogue(self):
        (self.root / 'dialogue.json').write_text(json.dumps({'dialogue': [], 'source_speech': ['第一句。', '第二句。'], 'stage1_speech': {'1A': [], '1B': []}}))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(self.root / 'dialogue.json'))
        self.s1.write_text(self.s1.read_text().replace('说：“第一句。”', '').replace('说：“第二句。”', ''))
        self.rejects(check1, self.s1, '剧本到对白清单遗漏')

    def test_unquoted_screenplay_dialogue_uses_exact_source_line_ranges(self):
        (self.root / 'source.md').write_text('甲\n第一句。\n\n乙\n第二句。\n')
        manifest = {'source_format': 'lines', 'source_ranges': [[2, 2], [5, 5]], 'dialogue': ['第一句。', '第二句。'], 'stage1_speech': {'1A': ['第一句。'], '1B': ['第二句。']}}
        (self.root / 'dialogue.json').write_text(json.dumps(manifest, ensure_ascii=False))
        self.field(self.s1, 'source_sha256', digest(self.root / 'source.md'))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(self.root / 'dialogue.json'))
        self.assertEqual(check1(self.s1), [])
        manifest['source_ranges'] = []
        (self.root / 'dialogue.json').write_text(json.dumps(manifest, ensure_ascii=False))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(self.root / 'dialogue.json'))
        self.rejects(check1, self.s1, '非空source_ranges')

    def test_inline_speaker_source_preserves_three_stage_chain(self):
        (self.root / 'source.md').write_text('甲：第一句。\n乙 : 第二句。\n')
        manifest = {'source_format': 'speaker_lines', 'source_ranges': [[1, 1], [2, 2]],
                    'source_speakers': ['甲', '乙'], 'dialogue': ['第一句。', '第二句。'], 'stage1_speech': {'1A': ['第一句。'], '1B': ['第二句。']}}
        (self.root / 'dialogue.json').write_text(json.dumps(manifest, ensure_ascii=False))
        self.field(self.s1, 'source_sha256', digest(self.root / 'source.md'))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(self.root / 'dialogue.json'))
        self.field(self.s2, 'stage1_sha256', digest(self.s1))
        self.bindings['source_stage1_sha256'] = digest(self.s1)
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage1_sha256', digest(self.s1))
        self.field(self.s3, 'stage2_sha256', digest(self.s2))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        for check, path in ((check1, self.s1), (check2, self.s2), (check3, self.s3)):
            self.assertEqual(check(path), [])
        for changes, message in [({'source_speakers': ['丙', '乙']}, '未匹配声明角色'),
                                 ({'source_speakers': ['甲']}, '逐段对应'),
                                 ({'source_speakers': [' ', '乙']}, '逐段对应'),
                                 ({'source_ranges': None}, 'source_ranges'),
                                 ({'source_ranges': [[1, 2], [2, 2]]}, '多次角色标签')]:
            with self.subTest(changes=changes):
                (self.root / 'dialogue.json').write_text(json.dumps({**manifest, **changes}))
                self.field(self.s1, 'dialogue_manifest_sha256', digest(self.root / 'dialogue.json'))
                self.rejects(check1, self.s1, message)

    def test_inline_speaker_extraction_preserves_colons_quotes_and_wrapped_speech(self):
        self.assertEqual(speaker_line_dialogue(
            ['  师  爷 ：理由：先等。\n', '他说“甲：别走。”\n', '再问：为什么？\n'], '师爷', ['师爷', '甲']),
            '理由：先等。 他说“甲：别走。” 再问：为什么？')
        self.assertEqual(speaker_line_dialogue([' Mary Jane : Wait: why?'], 'Mary Jane', ['Mary Jane']), 'Wait: why?')
        for block in [['甲：\n'], ['乙：话。'], ['甲：话。\n', '乙：另一句。'], ['甲：话。乙：另一句。']]:
            with self.subTest(block=block), self.assertRaises(ValueError):
                speaker_line_dialogue(block, '甲', ['甲', '乙'])

    def test_duplicate_categories_and_offblock_speech(self):
        self.row('1A', 4, lambda s: s.replace('画面：', '画面：甲说：“新增的第三句。”'))
        self.assertEqual(check3(self.s3), [])  # 未声明的声音须由对源内容审读识别
        self.row('1B', 4, lambda s: s.replace('<br><br>无配乐', '<br><br>动作/表情：乙说：“第二句。”<br><br>无配乐'))
        self.rejects(check3, self.s3, '必须且只能包含一个动作/表情')

    def test_focal_length_tokens_and_required_blocks(self):
        self.row('1A', 4, lambda s: s.replace('50mm', '150mm'))
        self.rejects(check3, self.s3, '毫米焦段数值或顺序')
        self.row('1B', 4, lambda s: s.replace('<br><br>声音：N/A。', ''))
        self.rejects(check3, self.s3, '必须且只能包含一个声音')

    def test_empty_or_mislabeled_cards_are_rejected(self):
        for body in ('', '背景：甲背后的窗。'):
            with self.subTest(body=body):
                self.s2.write_text(markdown(self.meta2, '### 光影·甲\n\n' + body))
                self.rejects(check2, self.s2, '正文必须非空')

    def test_dialogue_split_is_independent_of_optional_source_argument(self):
        self.row('1A', 4, lambda s: s.replace('“第一句。”', '“第一”停顿后说：“句。”'))
        self.bindings['shots']['1A']['speech'] = ['第一', '句。']
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.assertEqual(check3(self.s3), [])
        self.assertEqual(check3(self.s3, stage1_path=self.s1), [])

    def test_broken_source_rows_produce_errors_instead_of_crash(self):
        self.s1.write_text(self.s1.read_text().replace('| 1 | 1B | 4s |', '| 1 |'))
        errors = check3(self.s3, stage1_path=self.s1)
        self.assertTrue(errors)

    def test_context_words_in_protected_dialogue_are_allowed(self):
        self.replace_first_dialogue('我仍然相信你。')
        self.assertEqual(check3(self.s3), [])

    def replace_first_dialogue(self, dialogue):
        for path in (self.root / 'source.md', self.root / 'dialogue.json', self.s1, self.s3):
            text = path.read_text()
            if path.suffix == '.json':
                text = json.dumps({'dialogue': [dialogue, '第二句。'], 'source_speech': [dialogue, '第二句。'], 'stage1_speech': {'1A': [dialogue], '1B': ['第二句。']}}, ensure_ascii=False)
            else:
                text = text.replace('第一句。', dialogue)
            path.write_text(text)
        self.field(self.s1, 'source_sha256', digest(self.root / 'source.md'))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(self.root / 'dialogue.json'))
        self.field(self.s2, 'stage1_sha256', digest(self.s1))
        self.bindings['shots']['1A']['speech'] = [dialogue]
        self.bindings['source_stage1_sha256'] = digest(self.s1)
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.field(self.s3, 'stage1_sha256', digest(self.s1))
        self.field(self.s3, 'stage2_sha256', digest(self.s2))

    def test_format_words_in_original_dialogue_are_not_commands(self):
        self.replace_first_dialogue('报告上写着声音：一切正常。他看向画外的人是谁？导演要求无配乐无字幕。')
        self.assertEqual(check3(self.s3), [])

    def test_camera_position_motion_and_scale_changes(self):
        original = self.s3.read_text()
        for old, new, diagnostic in [('甲右前方', '甲左前方', '机位/肩侧'), ('甲的中景', '甲的中近景', '景别')]:
            with self.subTest(old=old):
                self.s3.write_text(original)
                self.row('1A', 4, lambda text: text.replace(old, new))
                self.assertEqual(check3(self.s3), [])  # 此处只证明字词差异不再自动阻塞，拍法是否改变须内容复核
        self.s3.write_text(original)
        self.row('1A', 4, lambda text: text.replace('固定镜头', '手持镜头'))
        errors, _, sources = checked_stage3(self.s3)
        self.assertEqual(errors, [])
        self.assertEqual([item['shot'] for item in sources['_content_warnings']], ['1A'])
        self.assertIn('摄影机运动', sources['_content_warnings'][0]['message'])

    def test_undeclared_speech_in_emotion_requires_content_review(self):
        self.row('1A', 4, lambda s: s.replace('<br><br>声音：', '<br><br>情感解析：甲说：“这是新增对白。”<br><br>声音：'))
        self.assertEqual(check3(self.s3), [])  # 不将关键词扫描伪装成发声完整性证明

    def test_inherited_phase_voice_format_remains_valid(self):
        self.s2.write_text(self.s2.read_text() + '\n### 阶段音色·甲·电话\n\n音色状态：持续电话滤波，语速稳定。\n')
        self.assertEqual(check2(self.s2), [])

    def test_sound_cards_require_selection_and_exact_positive_text(self):
        card = '声音：窗外持续雨声。'
        self.s2.write_text(self.s2.read_text() + '\n### 场次声音·雨\n\n' + card + '\n')
        self.field(self.s2, 'scene_sound_required', 'yes')
        self.field(self.s3, 'stage2_sha256', digest(self.s2))
        self.row('1A', 4, lambda text: text.replace('声音：N/A。', card))
        self.rejects(check3, self.s3, '未选择的固定声音')
        self.bindings['shots']['1A']['fixed_cards'] = ['场次声音·雨']
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.assertEqual(check3(self.s3), [])
        self.row('1A', 4, lambda text: text.replace('声音：窗外', '声音：没有窗外'))
        self.rejects(check3, self.s3, '未完整展开')
        self.assertTrue(contains_sound_unit('声音：脚步声。窗外持续雨声。椅子声。', card))
        self.assertFalse(contains_sound_unit('声音：没有窗外持续雨声。', card))

    def test_stage1_rejects_added_dialogue(self):
        self.s1.write_text(self.s1.read_text().replace('第一句。”', '第一句。”甲又说：“已删除的旧句。”'))
        manifest = self.root / 'dialogue.json'
        record = json.loads(manifest.read_text())
        record['stage1_speech']['1A'].append('已删除的旧句。')
        manifest.write_text(json.dumps(record, ensure_ascii=False))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(manifest))
        self.rejects(check1, self.s1, '新增、重复或旧版台词')

    def test_notes_revision_invalidates_stage1(self):
        (self.root / 'notes.md').write_text('人物换位后的记录')
        self.rejects(check1, self.s1, '连续性复核记录摘要')

    def test_stale_or_missing_stage2_source(self):
        self.field(self.s2, 'stage1_sha256', '0' * 64)
        self.rejects(check2, self.s2, 'Stage 1摘要')
        self.field(self.s2, 'source_stage1', 'missing.md')
        self.rejects(check2, self.s2, '文件不存在')

    def test_unapproved_upstream_is_rejected(self):
        self.field(self.s1, 'status', 'draft')
        self.field(self.s3, 'stage1_sha256', digest(self.s1))
        self.rejects(check3, self.s3, '来源必须是 approved')

    def test_source_override_must_match(self):
        other = self.root / 'other.md'
        other.write_text(self.s1.read_text())
        self.assertTrue(any('不是同一文件' in e for e in check3(self.s3, stage1_path=other)))

    def test_overlong_shot_and_bad_time_are_rejected(self):
        self.row('1A', 2, lambda _: '70s')
        self.row('1A', 4, lambda s: s.replace('0-4秒', '0-70秒'))
        errors = check3(self.s3)
        for fragment in ('不超过30秒', '时长与 Stage 1', '累计时间', '硬上限'):
            self.assertTrue(any(fragment in e for e in errors), errors)

    def test_complete_tail_cannot_disappear(self):
        self.s3.write_text('\n'.join(line for line in self.s3.read_text().splitlines() if not line.startswith('| 1B |')) + '\n')
        self.rejects(check3, self.s3, '完整、唯一且顺序一致')

    def test_bad_row_is_not_silently_skipped(self):
        self.s3.write_text(self.s3.read_text().replace('| 1B | 片段1 |', '| 1B |'))
        self.rejects(check3, self.s3, '不能跳过坏行')

    def test_stage3_storyboard_and_extra_dialogue(self):
        self.row('1A', 3, lambda _: '被改写的上游内容')
        self.rejects(check3, self.s3, '逐字合并')
        self.row('1B', 4, lambda s: s.replace('动作/表情：', '动作/表情：乙说：“旧句。”'))
        self.bindings['shots']['1B']['speech'].insert(0, '旧句。')
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.rejects(check3, self.s3, '新增、重复或旧版台词')

    def test_segment_number_time_and_upstream_identity(self):
        self.row('1A', 1, lambda _: '片段99')
        self.rejects(check3, self.s3, '编号必须从1')
        self.field(self.s3, 'project', 'another-project')
        self.rejects(check3, self.s3, 'project 与 Stage 1')

    def test_fixed_card_selection_and_text(self):
        light = '光影：主光源为甲右侧窗光，4000K，右侧亮面300 lux，左侧暗面100 lux，背景60 lux。'
        self.s2.write_text(self.s2.read_text() + '\n### 光影·甲\n\n' + light + '\n')
        self.field(self.s3, 'stage2_sha256', digest(self.s2))
        self.bindings['shots']['1A']['fixed_cards'] = ['光影·甲']
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.rejects(check3, self.s3, '固定条件正文')
        self.row('1A', 4, lambda s: s.replace('<br><br>无配乐', '<br><br>' + light + '<br><br>无配乐'))
        self.assertEqual(check3(self.s3), [])
        self.row('1A', 4, lambda s: s.replace('300 lux', '900 lux'))
        self.rejects(check3, self.s3, '固定条件正文')

    def test_recorded_single_shot_exception(self):
        text = '光影：主光源为甲右侧窗光，4000K，右侧亮面300 lux，左侧暗面100 lux，背景60 lux。'
        self.bindings['shots']['1A']['exceptions'] = {'光影': {'text': text, 'basis': '本镜单独面对窗光。', 'stage1_evidence': '甲站在画面中央'}}
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.row('1A', 4, lambda s: s.replace('<br><br>无配乐', '<br><br>' + text + '<br><br>无配乐'))
        self.assertEqual(check3(self.s3), [])
        self.bindings['shots']['1A']['exceptions']['光影']['stage1_evidence'] = '不存在的上游动作'
        self.bindings_path.write_text(json.dumps(self.bindings))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))
        self.rejects(check3, self.s3, '可追溯原文')

    def test_cli_checks_real_files(self):
        for script, args in [('validate_v2_shotlist.py', [str(self.s1)]), ('validate_v2_stage2.py', [str(self.s2)]),
                             ('validate_v2_prompts.py', ['--stage3', str(self.s3)])]:
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / script), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.row('1A', 2, lambda _: '70s')
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/validate_v2_prompts.py'), '--stage3', str(self.s3)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('30秒', result.stdout)


if __name__ == '__main__':
    unittest.main()
