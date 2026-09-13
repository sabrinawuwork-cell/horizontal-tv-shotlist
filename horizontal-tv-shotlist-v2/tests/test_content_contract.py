"""Regression checks for actual exports and explicit speech records.

Passing the automated checks deliberately does not certify unmarked speech,
acting, lighting meaning or spatial continuity; those require content review.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import unittest

import test_v2_delivery as fixtures
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from content_contract import prompt_blocks, select_speech
from generation_tasks import compile_delivery
from v2_delivery_checks import digest
from validate_v2_shotlist import validate_file as check1, declared_source_speech
from validate_v2_prompts import validate_stage3 as check3, prompt_errors, prompt_policy_errors
from v2_project_inputs import ProjectPolicy
from validate_shotlist import validate_shotlist_text


class ContentContractChecks(unittest.TestCase):
    setUp = fixtures.DeliveryChecks.setUp
    field = fixtures.DeliveryChecks.field
    row = fixtures.DeliveryChecks.row

    def sync(self):
        self.field(self.s1, 'source_sha256', digest(self.root / 'source.md'))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(self.root / 'dialogue.json'))
        self.field(self.s2, 'stage1_sha256', digest(self.s1))
        self.bindings['source_stage1_sha256'] = digest(self.s1)
        self.bindings_path.write_text(json.dumps(self.bindings, ensure_ascii=False))
        self.field(self.s3, 'stage1_sha256', digest(self.s1))
        self.field(self.s3, 'stage2_sha256', digest(self.s2))
        self.field(self.s3, 'stage3_bindings_sha256', digest(self.bindings_path))

    def exported(self):
        output = self.root / 'export'
        compile_delivery(self.s3, output)
        result = (output / 'task-001.txt').read_text()
        self.assertEqual(result.splitlines()[0], '全片要求：不添加背景配乐，不生成字幕。')
        self.assertEqual(result.strip().splitlines()[-1], '无配乐无字幕')
        return result

    def test_separate_cry_laugh_and_quoted_effect_export(self):
        self.row('1A', 4, lambda s: s.replace('动作/表情：', '动作/表情：甲含泪，')
                 .replace('声音：N/A。', '声音：窗外传来乙的笑声，门铃响起“叮咚”。'))
        self.sync()
        text = self.exported()
        self.assertIn('甲含泪', text)
        self.assertIn('乙的笑声', text)
        self.assertIn('“叮咚”', text)

    def test_sign_and_same_spoken_words_have_distinct_occurrences(self):
        source = self.root / 'source.md'
        source.write_text('门牌写着“禁止入内”。甲说：“禁止入内”。乙说：“第二句。”')
        record = {'dialogue': ['禁止入内', '第二句。'],
                  'source_speech': [{'text': '禁止入内', 'occurrence': 2}, '第二句。'],
                  'stage1_speech': {'1A': [{'text': '禁止入内', 'occurrence': 2}], '1B': ['第二句。']}}
        (self.root / 'dialogue.json').write_text(json.dumps(record, ensure_ascii=False))
        picture = '门牌写着“禁止入内”。甲说：“禁止入内”。'
        self.s1.write_text(self.s1.read_text().replace(self.pictures['1A'], picture))
        self.row('1A', 3, lambda s: s.replace(self.pictures['1A'], picture))
        self.row('1A', 4, lambda s: s.replace('动作/表情：甲说：“第一句。”', '动作/表情：' + picture))
        self.bindings['shots']['1A']['speech'] = [{'text': '禁止入内', 'occurrence': 2}]
        self.sync()
        self.assertEqual(check1(self.s1), [])
        self.assertEqual(check3(self.s3), [])
        self.assertIn(picture, self.exported())
        # Removing the explicit occurrence must not silently choose the sign.
        self.bindings['shots']['1A']['speech'] = ['禁止入内']
        self.sync()
        self.assertTrue(any('出现多次' in e for e in check3(self.s3)))

    def test_quote_category_words_and_blank_lines_are_literal(self):
        text = ('0-4秒。50mm。\n \n画面：纸上写着“声音：\n\n背景：白墙”。'
                '\n\n动作/表情：纸保持不动。\n\n声音：钟响“叮咚”。')
        self.assertEqual([b.label for b in prompt_blocks(text)], [None, '画面', '动作/表情', '声音'])
        self.assertEqual(prompt_errors(text, set()), [])
        self.assertTrue(prompt_errors(text.replace('\n\n动作/表情：', '动作/表情：'), set()))

    def test_equivalent_camera_wording_and_numeric_forms_export(self):
        for focal in ('8mm', '50.5mm', '35–70mm', '35mm，逐渐变焦至70mm'):
            with self.subTest(focal=focal):
                old1, old3 = self.s1.read_text(), self.s3.read_text()
                self.s1.write_text(old1.replace('50mm', focal))
                self.s3.write_text(old3.replace('50mm', focal))
                self.row('1A', 4, lambda s: s.replace('机位在甲右前方', '摄影机位于甲右前方'))
                self.sync()
                self.assertEqual(check1(self.s1), [])
                self.assertEqual(check3(self.s3), [])
                compile_delivery(self.s3, self.root / ('numeric-' + str(len(list(self.root.glob('numeric-*'))))))
                self.row('1A', 4, lambda s: s.replace(focal, '90mm', 1))
                self.assertTrue(any('焦段数值或顺序' in e for e in check3(self.s3)))
                self.s1.write_text(old1); self.s3.write_text(old3)

    def test_two_extras_cards_merge_without_absorbing_notes(self):
        self.field(self.s2, 'extras_required', 'yes')
        self.s2.write_text(self.s2.read_text() + '\n### 群演外观·候场者\n\n群演：两名候场者穿灰色外套。'
                          '\n\n### 群演动作·候场者\n\n群演：两人坐着翻阅资料。\n\n## 复核说明\n\n这段说明不得进入任务。\n')
        self.bindings['shots']['1A']['fixed_cards'] = ['群演外观·候场者', '群演动作·候场者']
        self.row('1A', 4, lambda s: s.replace('<br><br>无配乐', '<br><br>群演：两名候场者穿灰色外套。'
                 '<br><br>群演：两人坐着翻阅资料。<br><br>无配乐'))
        self.sync()
        self.assertEqual(check3(self.s3), [])
        result = self.exported()
        self.assertEqual(result.count('群演：'), 1)
        self.assertIn('两名候场者穿灰色外套。\n两人坐着翻阅资料。', result)
        self.assertNotIn('这段说明', result)
        self.row('1A', 4, lambda s: s.replace('两人坐着翻阅资料。', '两人站着翻阅资料。'))
        self.assertTrue(any('固定条件正文' in e for e in check3(self.s3)))

    def test_missing_or_false_records_do_not_claim_dialogue_success(self):
        original = (self.root / 'dialogue.json').read_text()
        for record in (['第一句。', '第二句。'],
                       {'dialogue': ['第一句。', '第二句。'], 'source_speech': ['第一句。', '第二句。']},
                       {'dialogue': [], 'source_speech': ['第一句。', '第二句。'], 'stage1_speech': {'1A': [], '1B': []}}):
            with self.subTest(record=record):
                (self.root / 'dialogue.json').write_text(json.dumps(record, ensure_ascii=False))
                self.sync()
                self.assertTrue(check1(self.s1))
                self.assertTrue(check3(self.s3))
        (self.root / 'dialogue.json').write_text(original)
        self.sync()
        self.bindings['shots']['1A']['speech'] = []
        self.sync()
        self.assertTrue(any('遗漏' in e for e in check3(self.s3)))

    def test_declared_duplicates_order_and_positions_remain_strict(self):
        for entries in (['甲', '乙'], [{'text':'甲','occurrence':2}, '乙'],
                        [{'text':'甲','occurrence':1}, {'text':'甲','occurrence':1}]):
            self.assertTrue(select_speech('甲乙甲', entries, '定位')[2])
        self.assertEqual(select_speech('甲乙甲', [{'text':'甲','occurrence':1}, '乙', {'text':'甲','occurrence':2}], '定位')[0], ['甲','乙','甲'])
        errors = []
        declared_source_speech({'source_speech': [], 'source_ranges': [[1,1]]}, '文字', errors)
        self.assertTrue(errors)

    def test_quote_marks_do_not_exempt_nondialogue_project_terms(self):
        text = '0-4秒。50mm。\n\n画面：标牌写着“红军”。\n\n动作/表情：甲说：“红军”。\n\n声音：N/A。'
        errors = prompt_policy_errors(text, ['红军'], {'红军'}, ProjectPolicy())
        self.assertIn('非对白敏感词未处理：红军', errors)

    def test_invalid_manifest_is_reported_without_second_parse_crash(self):
        (self.root / 'dialogue.json').write_text('{invalid')
        errors, _ = validate_shotlist_text(self.s1.read_text(), dialogue_manifest=self.root / 'dialogue.json', asset_root=self.root)
        self.assertTrue(any('对白清单错误' in e for e in errors))
