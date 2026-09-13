"""Actual Stage 1 -> 2/3 -> task/XLSX behavior for stable revision IDs."""
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
from test_workbook_update import make_workbook, mutate_sheet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from shotlist_common import parse_frontmatter, read_text
from v2_delivery_checks import digest, main_rows
from shot_revision import build_mapping, numbering_errors, _replace_ids
from validate_v2_shotlist import validate_file as check1
from validate_v2_stage2 import validate_file as check2
from validate_v2_prompts import validate_stage3 as check3
import generation_tasks as generation
import update_workbook as updater


class ShotRevisionTests(unittest.TestCase):
    field = fixtures.DeliveryChecks.field

    def setUp(self):
        fixtures.DeliveryChecks.setUp(self)
        # Silent real-file fixture isolates revision behavior from dialogue edits.
        source, dialogue = self.root / 'source.md', self.root / 'dialogue.json'
        source.write_text('甲乙隔桌相对。')
        dialogue.write_text(json.dumps({'dialogue': [], 'source_speech': [], 'stage1_speech': {'1A': [], '1B': []}}))
        text = self.s1.read_text().replace('说：“第一句。”', '站定。').replace('说：“第二句。”', '站定。')
        self.s1.write_text(text)
        self.field(self.s1, 'source_sha256', digest(source))
        self.field(self.s1, 'dialogue_manifest_sha256', digest(dialogue))
        self.old_rows = main_rows(self.s1.read_text())
        self.base = self.root / 'stage1-v001.md'
        self.base.write_bytes(self.s1.read_bytes())
        self.record = self.root / 'revision.json'
        self.output = self.root / 'tasks'
        self.book = self.root / 'original.xlsx'
        make_workbook(self.book)
        self.spec = dict(workbook=str(self.book), sheet='目标表', header_row=3, start_row=5,
                         writable_range='B5:H10', columns=dict(zip(updater.HEADERS, 'BCDEFGH')),
                         shots=['1A', '1B'], expected_existing_shots=['1A', '1B'], protected_ranges=['A4:J4'])
        self.sync()

    def meta(self, path, key, value):
        text = path.read_text()
        if re.search(rf'(?m)^{key}:', text):
            self.field(path, key, value)
        else:
            path.write_text(text.replace('\n---\n', f'\n{key}: {value}\n---\n', 1))

    def write_rows(self, rows):
        text = self.s1.read_text()
        head = next(line for line in text.splitlines() if line.startswith('| ---'))
        prefix = text.split(head, 1)[0] + head + '\n'
        self.s1.write_text(prefix + ''.join('| ' + ' | '.join(row) + ' |\n' for row in rows))
        total = sum(float(row[2].rstrip('s')) for row in rows)
        self.field(self.s1, 'total_duration', f'{total:g}s')
        self.s1.write_text(re.sub(r'场次时长：[^\n]+', f'场次时长：{total:g}s', self.s1.read_text()))

    def row(self, number, **changes):
        row = list(self.old_rows[number])
        for column, value in changes.items():
            row[int(column)] = value
        return tuple(row)

    def bind(self, changes=None, mode='local', base=None, record=None):
        base, record = base or self.base, record or self.record
        changes = changes or []
        payload = dict(version=1, mode=mode, reason='按本次明确反馈修订', base_stage1=base.name,
                       base_stage1_sha256=digest(base), changes=changes,
                       mapping=build_mapping(base.read_text(), self.s1.read_text(), mode, changes))
        if mode == 'renumber':
            old_front = parse_frontmatter(base.read_text()).values
            old_notes = base.parent / old_front['continuity_notes']
            ids = {entry['old']: entry['new'] for entry in payload['mapping']}
            notes = self.root / (record.stem + '-notes.md')
            notes.write_text(_replace_ids(old_notes.read_text(), ids))
            self.meta(self.s1, 'continuity_notes', notes.name)
            self.meta(self.s1, 'continuity_notes_sha256', digest(notes))
        record.write_text(json.dumps(payload, ensure_ascii=False))
        self.meta(self.s1, 'revision_record', record.name)
        self.meta(self.s1, 'revision_record_sha256', digest(record))
        self.sync()
        return payload

    def sync(self):
        rows = main_rows(self.s1.read_text())
        # Keep each historical source record immutable for revision audit.
        dialogue = self.root / f'dialogue-{len(list(self.root.glob("dialogue-*.json")))}.json'
        dialogue.write_text(json.dumps({'dialogue': [], 'source_speech': [], 'stage1_speech': {row[1]: [] for row in rows}}))
        self.field(self.s1, 'dialogue_manifest', dialogue.name)
        self.field(self.s1, 'dialogue_manifest_sha256', digest(dialogue))
        self.field(self.s2, 'stage1_sha256', digest(self.s1))
        rows = main_rows(self.s1.read_text())
        self.bindings = {'source_stage1_sha256': digest(self.s1), 'shots': {row[1]: {'fixed_cards': [], 'speech': []} for row in rows}}
        self.bindings_path.write_text(json.dumps(self.bindings))
        for key, value in [('stage1_sha256', digest(self.s1)), ('stage2_sha256', digest(self.s2)), ('stage3_bindings_sha256', digest(self.bindings_path))]:
            self.field(self.s3, key, value)
        text = self.s3.read_text()
        head = next(line for line in text.splitlines() if line.startswith('| ---'))
        text = text.split(head, 1)[0] + head + '\n'
        elapsed = 0
        for row in rows:
            end = elapsed + float(row[2].rstrip('s'))
            prompt = (f'{elapsed:g}-{end:g}秒。{row[3]}<br><br>画面：甲乙保持起幅站位。'
                      f'<br><br>动作/表情：{row[4]}镜头最终停在人物站在画面中央的状态。'
                      f'<br><br>声音：{row[5]}。<br><br>无配乐无字幕')
            story = f'摄影机：{row[3]}<br><br>画面内容：{row[4]}<br><br>音效：{row[5]}'
            text += '| ' + ' | '.join((row[1], '片段1', row[2], story, prompt)) + ' |\n'
            elapsed = end
        self.s3.write_text(text)

    def insertion(self, *, position='after'):
        inserted = self.row(0, **{'1': '1A-1', '4': '甲站在画面中央，目光落向桌面。'})
        rows = [self.old_rows[0], inserted, self.old_rows[1]] if position == 'after' else [inserted, *self.old_rows]
        self.write_rows(rows)
        changes = [dict(kind='insert', shot='1A-1', reason='新增桌面反应', anchor='1A', position=position)]
        self.bind(changes)
        return changes

    def assert_valid_chain(self):
        for validator, path in ((check1, self.s1), (check2, self.s2), (check3, self.s3)):
            self.assertEqual(validator(path), [], str(path))

    def test_new_draft_still_rejects_gaps_or_suffixes_without_revision(self):
        for shot in ('1C', '1A-1'):
            with self.subTest(shot=shot):
                self.write_rows([self.old_rows[0], self.row(1, **{'1': shot})])
                self.assertTrue(any('连续' in e or '归一化' in e for e in check1(self.s1)))

    def test_local_inserted_ids_survive_three_stages_and_real_task_export(self):
        self.insertion()
        self.assert_valid_chain()
        generation.compile_delivery(self.s3, self.output)
        result = json.loads((self.output / 'edit-map.json').read_text())
        self.assertEqual([c['shot'] for c in result['clips']], ['1A', '1A-1', '1B'])
        self.assertEqual([(r['old'], r['new']) for r in result['shot_revision']['mapping']], [('1A','1A'), (None,'1A-1'), ('1B','1B')])
        inputs = {r['path'] for r in result['sources']['inputs']}
        self.assertTrue({str(self.base.resolve()), str(self.record.resolve())} <= inputs)

    def test_explicit_insert_before_first_keeps_existing_ids_and_order(self):
        self.insertion(position='before')
        self.assert_valid_chain()

    def test_delete_original_base_retains_previously_inserted_shot_with_full_history(self):
        self.insertion()
        previous = self.root / 'stage1-v002.md'
        previous.write_bytes(self.s1.read_bytes())
        rows = main_rows(self.s1.read_text())[1:]
        self.write_rows(rows)
        self.bind([dict(kind='delete', shot='1A', reason='明确删除先前主镜')], base=previous, record=self.root / 'revision2.json')
        self.assert_valid_chain()
        generation.compile_delivery(self.s3, self.output)
        records = json.loads((self.output / 'edit-map.json').read_text())['sources']['inputs']
        self.assertTrue({str(self.base.resolve()), str(previous.resolve()), str(self.record.resolve()), str((self.root / 'revision2.json').resolve())} <= {r['path'] for r in records})

    def test_changed_row_requires_exact_declared_scope(self):
        self.write_rows([self.old_rows[0], self.row(1, **{'4': '乙站在画面中央抬起下巴。'})])
        self.bind([dict(kind='modify', shot='1B', reason='调整应答表演')])
        self.assert_valid_chain()
        self.s1.write_text(self.s1.read_text().replace('甲站在画面中央站定。', '甲站在画面中央摇头。'))
        self.assertTrue(any('未声明修改' in e for e in check1(self.s1)), check1(self.s1))

    def test_empty_partial_mismatched_and_stale_revision_maps_reject(self):
        self.insertion()
        original = self.record.read_text()
        for mutation in ('missing', 'stale-row', 'unknown'):
            with self.subTest(mutation=mutation):
                payload = json.loads(original)
                if mutation == 'missing': payload['mapping'].pop()
                if mutation == 'stale-row': payload['mapping'][0]['new_row_sha256'] = '0' * 64
                if mutation == 'unknown': payload['extra_bypass'] = True
                self.record.write_text(json.dumps(payload))
                self.field(self.s1, 'revision_record_sha256', digest(self.record))
                self.assertTrue(check1(self.s1))

    def test_record_pair_and_old_snapshot_digest_cannot_be_omitted_or_refreshed_silently(self):
        self.insertion()
        original = self.s1.read_text()
        self.s1.write_text(re.sub(r'(?m)^revision_record_sha256:.*\n', '', original))
        self.assertTrue(any('修订记录摘要' in e for e in check1(self.s1)))
        self.s1.write_text(original)
        self.base.write_text(self.base.read_text() + '\n新内容\n')
        for validator, path in ((check1, self.s1), (check2, self.s2), (check3, self.s3)):
            self.assertTrue(any('修订旧 Stage 1摘要' in e for e in validator(path)))

    def test_duplicate_unexplained_rename_reorder_or_fictional_scope_rejected(self):
        for rows, changes in [
            ([self.old_rows[0], self.old_rows[0]], []),
            ([self.old_rows[1], self.old_rows[0]], []),
            ([self.old_rows[0], self.row(1, **{'1':'1C'})], [dict(kind='delete',shot='1B',reason='x'),dict(kind='insert',shot='1C',reason='x',anchor='1A',position='after')]),
            (self.old_rows, [dict(kind='modify',shot='1A',reason='没有实际变化')]),
        ]:
            with self.subTest(rows=rows):
                self.write_rows(rows)
                with self.assertRaises(ValueError):
                    build_mapping(self.base.read_text(), self.s1.read_text(), 'local', changes)

    def test_renaming_only_cannot_be_disguised_as_delete_and_insert(self):
        self.write_rows([self.old_rows[0], self.row(1, **{'1':'1B-1'})])
        changes = [dict(kind='delete',shot='1B',reason='x'),dict(kind='insert',shot='1B-1',reason='x',anchor='1B',position='after')]
        with self.assertRaisesRegex(ValueError, '伪装'):
            build_mapping(self.base.read_text(), self.s1.read_text(), 'local', changes)

    def test_old_source_requires_nonempty_approved_same_project_and_valid_numbering(self):
        self.insertion()
        original = self.base.read_text()
        for before, after in [('status: approved','status: draft'), ('project: fixture','project: another'),
                              ('episode: E1','episode: E2'), ('| 1 | 1A |','| 1 | 1D |')]:
            with self.subTest(after=after):
                self.base.write_text(original.replace(before, after))
                payload = json.loads(self.record.read_text())
                payload['base_stage1_sha256'] = digest(self.base)
                self.record.write_text(json.dumps(payload))
                self.field(self.s1,'revision_record_sha256',digest(self.record))
                self.assertTrue(check1(self.s1))
        self.base.write_text(original.split('| 1 |', 1)[0])
        payload['base_stage1_sha256'] = digest(self.base)
        self.record.write_text(json.dumps(payload))
        self.field(self.s1,'revision_record_sha256',digest(self.record))
        self.assertTrue(any('为空' in e for e in check1(self.s1)))

    def test_stage3_cannot_declare_its_own_exception_or_keep_stale_bindings(self):
        self.insertion()
        self.s3.write_text(self.s3.read_text().replace('| 1A-1 |','| 1C |'))
        self.meta(self.s3,'revision_record',self.record.name)
        self.meta(self.s3,'revision_record_sha256',digest(self.record))
        self.assertTrue(any('主序列完整' in e for e in check3(self.s3)))
        self.sync()
        self.field(self.s2,'stage1_sha256',digest(self.base))
        self.assertTrue(any('Stage 1摘要' in e for e in check2(self.s2)))
        self.assertTrue(check3(self.s3))

    def test_complete_renumbering_allows_exact_cross_refs_but_preserves_dialogue(self):
        self.insertion()
        previous = self.root / 'stage1-v002.md'
        # Cross reference including literal ID-like quoted speech must not be rewritten.
        rows = main_rows(self.s1.read_text())
        first = list(rows[0]); first[6] = '动作切到 1B；保留屏上标记“1B”。'
        rows[0] = tuple(first)
        self.write_rows(rows)
        self.bind([dict(kind='modify',shot='1A',reason='明确剪辑引用'),
                   dict(kind='insert',shot='1A-1',reason='新增反应',anchor='1A',position='after')])
        previous.write_bytes(self.s1.read_bytes())
        renumbered = [tuple([r[0], shot, *r[2:]]) for r, shot in zip(rows, ['1A','1B','1C'])]
        first = list(renumbered[0]); first[6] = '动作切到 1C；保留屏上标记“1B”。'; renumbered[0] = tuple(first)
        self.write_rows(renumbered)
        self.bind(mode='renumber',base=previous,record=self.root/'renumber.json')
        self.assert_valid_chain()
        self.assertEqual([(r['old'],r['new']) for r in json.loads((self.root/'renumber.json').read_text())['mapping']], [('1A','1A'),('1A-1','1B'),('1B','1C')])
        self.s1.write_text(self.s1.read_text().replace('标记“1B”','标记“1C”'))
        self.assertTrue(any('只允许' in e for e in check1(self.s1)))

    def test_renumber_dialogue_record_allows_only_mapped_keys(self):
        self.insertion()
        previous = self.root / 'stage1-v002.md'
        previous.write_bytes(self.s1.read_bytes())
        rows = main_rows(self.s1.read_text())
        self.write_rows([tuple([r[0], shot, *r[2:]]) for r, shot in zip(rows, ['1A', '1B', '1C'])])
        self.bind(mode='renumber', base=previous, record=self.root / 'renumber.json')
        self.assert_valid_chain()
        front = parse_frontmatter(self.s1.read_text()).values
        manifest = self.root / front['dialogue_manifest']
        original = manifest.read_text()
        for field, value in [('dialogue', ['新增对白']), ('source_speech', ['新增对白']),
                             ('stage1_speech', {'1A': [], '1B': ['新增对白'], '1C': []})]:
            with self.subTest(field=field):
                record = json.loads(original)
                record[field] = value
                manifest.write_text(json.dumps(record, ensure_ascii=False))
                self.field(self.s1, 'dialogue_manifest_sha256', digest(manifest))
                errors, _ = numbering_errors(self.s1)
                self.assertTrue(any('只能同步镜号' in e for e in errors), errors)
        manifest.write_text(original)
        self.field(self.s1, 'dialogue_manifest_sha256', digest(manifest))
        # The old record is part of the protected read set, not just the new one.
        from generation_tasks import _InputSnapshot
        snapshot = _InputSnapshot(self.s3)
        old_front = parse_frontmatter(previous.read_text()).values
        old_record = previous.parent / old_front['dialogue_manifest']
        old_record.write_text(old_record.read_text() + '\n')
        with self.assertRaises(ValueError):
            snapshot.verify_unchanged()

    def test_renumbering_rejects_reordering_content_edits_or_partial_map(self):
        self.insertion()
        previous = self.root / 'stage1-v002.md'; previous.write_bytes(self.s1.read_bytes())
        rows = main_rows(self.s1.read_text())
        new = [tuple([r[0], s, *r[2:]]) for r, s in zip(rows,['1A','1B','1C'])]
        wrong = list(new); changed = list(wrong[1]); changed[4] += '甲微笑。'; wrong[1] = tuple(changed)
        self.write_rows(wrong)
        with self.assertRaisesRegex(ValueError, '全部内容'):
            build_mapping(previous.read_text(), self.s1.read_text(), 'renumber', [])
        self.write_rows(new)
        payload = self.bind(mode='renumber',base=previous,record=self.root/'renumber.json')
        payload['mapping'].pop()
        (self.root/'renumber.json').write_text(json.dumps(payload))
        self.field(self.s1,'revision_record_sha256',digest(self.root/'renumber.json'))
        self.assertTrue(any('mapping' in e for e in check1(self.s1)))

    def test_renumbering_preserves_typed_authorization_and_coverage_targets(self):
        self.insertion()
        # The old 1B is a legitimate over-four actor authorization and COV base.
        self.meta(self.s1, 'active_cast_authorizations', '1B')
        coverage = '\n### 必要覆盖\n\n| 场次 | 镜号 | 时长 | 摄影机 | 画面内容 | 音效 | 剪辑衔接 |\n| --- | --- | --- | --- | --- | --- | --- |\n'
        cov = self.row(1, **{'1':'1B-COV1','6':'保护 1B 的接点风险。'})
        self.s1.write_text(self.s1.read_text() + coverage + '| ' + ' | '.join(cov) + ' |\n')
        previous = self.root / 'stage1-v002.md'; previous.write_bytes(self.s1.read_bytes())
        rows = main_rows(self.s1.read_text())
        self.write_rows([tuple([r[0], shot, *r[2:]]) for r,shot in zip(rows,['1A','1B','1C'])])
        new_cov = list(cov); new_cov[1] = '1C-COV1'; new_cov[6] = '保护 1C 的接点风险。'
        self.s1.write_text(self.s1.read_text() + coverage + '| ' + ' | '.join(new_cov) + ' |\n')
        self.meta(self.s1, 'active_cast_authorizations', '1C')
        self.bind(mode='renumber',base=previous,record=self.root/'renumber.json')
        self.assert_valid_chain()
        self.field(self.s1, 'active_cast_authorizations', '1B')
        self.assertTrue(any('授权转给别的镜头' in e for e in check1(self.s1)))
        self.field(self.s1, 'active_cast_authorizations', '1C')
        self.s1.write_text(self.s1.read_text().replace('1C-COV1','1B-COV1'))
        self.assertTrue(any('覆盖表必须完整' in e for e in check1(self.s1)))

    def test_renumber_notes_must_be_actual_unchanged_old_and_mapped_new_files(self):
        self.insertion()
        previous = self.root/'stage1-v002.md'; previous.write_bytes(self.s1.read_bytes())
        rows = main_rows(self.s1.read_text())
        self.write_rows([tuple([r[0], shot, *r[2:]]) for r,shot in zip(rows,['1A','1B','1C'])])
        self.bind(mode='renumber',base=previous,record=self.root/'renumber.json')
        self.assert_valid_chain()
        generation.compile_delivery(self.s3,self.output)
        dependencies = {r['path'] for r in json.loads((self.output/'edit-map.json').read_text())['sources']['inputs']}
        self.assertIn(str((self.root/'notes.md').resolve()),dependencies)
        current_notes = self.root/'renumber-notes.md'
        current_notes.write_text((self.root/'notes.md').read_text())
        self.field(self.s1,'continuity_notes_sha256',digest(current_notes))
        self.assertTrue(any('连续性记录必须' in e for e in check1(self.s1)))

    def test_malformed_record_field_types_report_errors_without_traceback(self):
        self.insertion()
        original = self.record.read_text()
        for field,value in [('base_stage1', []), ('changes', {}), ('mapping', {}), ('mode', []), ('reason', None), ('version', True)]:
            with self.subTest(field=field):
                payload=json.loads(original); payload[field]=value
                self.record.write_text(json.dumps(payload)); self.field(self.s1,'revision_record_sha256',digest(self.record))
                self.assertTrue(check1(self.s1))

    def test_cli_derives_mapping_from_actual_rows_without_changing_authority(self):
        self.insertion()
        changes = json.loads(self.record.read_text())['changes']
        change_path = self.root/'changes.json'; change_path.write_text(json.dumps(changes))
        out = self.root/'generated-record.json'
        before = {p:p.read_bytes() for p in (self.s1,self.base)}
        result = subprocess.run([sys.executable,str(ROOT/'scripts/shot_revision.py'),'--base',str(self.base),
            '--current',str(self.s1),'--mode','local','--changes',str(change_path),'--reason','新增反应','--output',str(out)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(out.read_text())['mapping'],json.loads(self.record.read_text())['mapping'])
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_existing_workbook_accepts_retained_ids_and_exposes_complete_mapping(self):
        self.insertion()
        # One explicitly empty existing slot accommodates the inserted row.
        mutate_sheet(self.book, lambda text: re.sub(r'<c r="([B-H])7"[^>]*>.*?</c>',
                          lambda m: '<c r="' + m[1] + '7" s="1"/>', text))
        self.spec['shots'] = ['1A','1A-1','1B']
        plan = updater.plan_update(self.s1,self.spec)
        self.assertIn('shot_revision',plan)
        original = self.book.read_bytes()
        self.assertNotEqual(plan['changed_cells'],0)
        self.assertEqual(updater.apply_update(plan)['status'],'saved')
        book = updater.Book(self.book.read_bytes(),'目标表')
        self.assertEqual(book.value('C5'),'1A')
        self.assertEqual(book.value('C6'),'1A-1')
        self.assertEqual(book.value('C7'),'1B')
        self.assertEqual(book.value('C4'),'FIRST-KEEP')
        self.assertNotEqual(self.book.read_bytes(),original)

    def test_workbook_revision_cannot_overwrite_a_surviving_unselected_old_shot(self):
        self.insertion()
        self.spec['shots'] = ['1A','1A-1']
        original = self.book.read_bytes()
        with self.assertRaisesRegex(updater.UpdateError, '不能丢弃'):
            updater.plan_update(self.s1,self.spec)
        self.assertEqual(self.book.read_bytes(),original)

    def test_stage3_original_workbook_accepts_exact_revision_mapping(self):
        self.write_rows([self.old_rows[0],self.row(1, **{'4':'乙站在画面中央抬起下巴。'})])
        self.bind([dict(kind='modify',shot='1B',reason='修改表演')])
        make_workbook(self.book,stage3=True)
        self.spec['columns'] = dict(zip(updater.STAGE3_HEADERS,'BCDEF'))
        self.spec['writable_range'] = 'B5:F10'
        plan = updater.plan_update(self.s3,self.spec)
        self.assertEqual(len(plan['shot_revision']['mapping']),2)
        self.assertEqual(updater.apply_update(plan)['status'],'saved')
        book = updater.Book(self.book.read_bytes(),'目标表')
        self.assertEqual(book.value('B6'),'1B')
        self.assertIn('抬起下巴',book.value('E6'))
        self.assertEqual(book.value('B4'),'FIRST-KEEP')

    def test_task_export_rejects_revision_dependency_thread_change_or_change_restore(self):
        self.insertion()
        for dependency in (self.base,self.record):
            original = dependency.read_bytes()
            for restore in (False,True):
                with self.subTest(dependency=dependency.name,restore=restore):
                    check = generation._InputSnapshot.verify_unchanged
                    mutated = False
                    def race(snapshot):
                        nonlocal mutated
                        if not mutated:
                            mutated = True
                            def change():
                                dependency.write_bytes(original+b'\n')
                                if restore: dependency.write_bytes(original)
                            thread=threading.Thread(target=change);thread.start();thread.join()
                        return check(snapshot)
                    with patch.object(generation._InputSnapshot,'verify_unchanged',race):
                        with self.assertRaisesRegex(ValueError,'输入已变化'):
                            generation.compile_delivery(self.s3,self.output)
                    self.assertFalse(self.output.exists())
                    dependency.write_bytes(original)

    def test_workbook_preview_apply_pins_revision_dependencies_including_restore(self):
        self.insertion()
        for dependency in (self.base,self.record):
            with self.subTest(dependency=dependency.name):
                plan=updater.plan_update(self.s1,self.spec)
                self.assertIn(str(dependency.resolve()),{r['path'] for r in plan['input_dependencies']})
                original=dependency.read_bytes(); book=self.book.read_bytes()
                dependency.write_bytes(original+b'\n');dependency.write_bytes(original)
                with self.assertRaisesRegex(updater.UpdateError,'旧预览'):
                    updater.apply_update(plan)
                self.assertEqual(self.book.read_bytes(),book)

    def test_workbook_rechecks_revision_during_actual_write_and_preserves_target(self):
        self.insertion()
        plan=updater.plan_update(self.s1,self.spec)
        original=self.record.read_bytes();book=self.book.read_bytes()
        build=updater._build_bytes
        def race(*args):
            data=build(*args)
            def change(): self.record.write_bytes(original+b'\n');self.record.write_bytes(original)
            thread=threading.Thread(target=change);thread.start();thread.join()
            return data
        with patch.object(updater,'_build_bytes',race):
            with self.assertRaisesRegex(updater.UpdateError,'准备写入期间'):
                updater.apply_update(plan)
        self.assertEqual(self.book.read_bytes(),book)


if __name__ == '__main__':
    unittest.main()
