from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import update_workbook as updater
import test_v2_delivery


def cell(address, value, *, formula=None, style='1'):
    if formula is not None:
        return f'<c r="{address}" s="{style}"><f>{escape(formula)}</f><v>8</v></c>'
    if value is None:
        return f'<c r="{address}" s="{style}"/>'
    if isinstance(value, (int, float)):
        return f'<c r="{address}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'


def make_workbook(path, *, stage3=False, reserved=False):
    """Generate a real, minimal OOXML workbook; no production file is read."""
    ns, rel = updater.NS, updater.REL_NS
    headers = updater.STAGE3_HEADERS if stage3 else updater.HEADERS
    column_letters = 'BCDEF' if stage3 else 'BCDEFGH'
    id_column = 'B' if stage3 else 'C'
    rows = ['<row r="1" ht="36" customHeight="1">' + cell('A1', '原始标题，不能改') + '</row>',
            '<row r="3" ht="28" customHeight="1">' + ''.join(f'<c r="{col}3" s="1" t="s"><v>{i}</v></c>' for i, col in enumerate(column_letters)) + '</row>']
    for number, shot in ((4, 'FIRST-KEEP'), (5, '1A'), (6, '1B'), (7, 'LATER-KEEP')):
        values = {col: f'原{col}{number}' for col in column_letters}
        values[id_column] = shot
        values['D'] = 99
        if number == 6 and reserved:
            values = {col: None for col in column_letters}
        contents = cell(f'A{number}', '用户批注' + str(number))
        contents += ''.join(cell(f'{col}{number}', value, style='2' if col == 'D' else '1') for col, value in values.items())
        contents += cell(f'I{number}', None, formula='D5+D6') if number == 5 else cell(f'I{number}', '保留侧栏')
        rows.append(f'<row r="{number}" ht="68" customHeight="1">{contents}</row>')
    rows.append('<row r="12">' + cell('J12', '表尾保护内容') + '</row>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{ns}" xmlns:r="{rel}">'
             '<dimension ref="A1:J12"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="3" topLeftCell="A4" state="frozen"/></sheetView></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15"/><cols><col min="2" max="8" width="30" customWidth="1"/></cols>'
             '<sheetData>\n<!-- literal outside cells must survive -->\n' + '\n'.join(rows) + '\n</sheetData>'
             '<mergeCells count="1"><mergeCell ref="A1:H1"/></mergeCells></worksheet>')
    other = f'<worksheet xmlns="{ns}"><dimension ref="A1:B2"/><sheetData><row r="1">{cell("A1", "其他工作表")}</row><row r="2">{cell("B2", None, formula="\u0027目标表\u0027!D5+\u0027目标表\u0027!D6")}</row></sheetData></worksheet>'
    styles = (f'<styleSheet xmlns="{ns}"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.0&quot;s&quot;"/></numFmts>'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="12"/><color rgb="FF123456"/><name val="Aptos"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEEEEFF"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf>'
              '<xf numFmtId="164" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    types = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>', '<Default Extension="xml" ContentType="application/xml"/>']
    for part, content in (('workbook.xml', 'sheet.main'), ('worksheets/sheet1.xml', 'worksheet'), ('worksheets/sheet2.xml', 'worksheet'), ('styles.xml', 'styles'), ('sharedStrings.xml', 'sharedStrings')):
        types.append(f'<Override PartName="/xl/{part}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.{content}+xml"/>')
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' + ''.join(types) + '</Types>',
        '_rels/.rels': f'<Relationships xmlns="{updater.PKG_NS}"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="目标表" sheetId="1" r:id="rId1"/><sheet name="其他表" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{updater.PKG_NS}">' + ''.join(f'<Relationship Id="rId{i}" Type="{rel}/{kind}" Target="{target}"/>' for i, kind, target in ((1, 'worksheet', 'worksheets/sheet1.xml'), (2, 'worksheet', 'worksheets/sheet2.xml'), (3, 'styles', 'styles.xml'), (4, 'sharedStrings', 'sharedStrings.xml'))) + '</Relationships>',
        'xl/worksheets/sheet1.xml': sheet,
        'xl/worksheets/sheet2.xml': other,
        'xl/styles.xml': styles,
        'xl/sharedStrings.xml': f'<sst xmlns="{ns}" count="{len(headers)}" uniqueCount="{len(headers)}">' + ''.join(f'<si><t>{escape(h)}</t></si>' for h in headers) + '</sst>',
        'customXml/item1.xml': '<preserve>原文件自定义数据</preserve>',
    }
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b'original archive comment'
        for name, contents in parts.items():
            archive.writestr(name, contents.encode('utf-8'))


def parts(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def mutate_sheet(path, transform):
    contents = parts(path)
    contents['xl/worksheets/sheet1.xml'] = transform(contents['xl/worksheets/sheet1.xml'].decode()).encode()
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            archive.writestr(name, content)


class WorkbookUpdateTests(unittest.TestCase):
    def setUp(self):
        # Existing three-stage fixture creation binds real temporary source files.
        self.source = test_v2_delivery.DeliveryChecks()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.root = self.source.root
        self.path = self.root / '明确指定的现有工作簿.xlsx'
        make_workbook(self.path)
        self.spec = dict(workbook=str(self.path), sheet='目标表', header_row=3, start_row=5,
                         writable_range='B5:H10', columns=dict(zip(updater.HEADERS, 'BCDEFGH')),
                         shots=['1A', '1B'], expected_existing_shots=['1A', '1B'], protected_ranges=['A4:J4', 'J12'])

    def plan(self, **changes):
        spec = copy.deepcopy(self.spec)
        spec.update(changes)
        return updater.plan_update(self.source.s1, spec)

    def rejects_without_write(self, text, **changes):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(updater.UpdateError, text):
            self.plan(**changes)
        self.assertEqual(self.path.read_bytes(), before)

    def test_preview_is_read_only_and_maps_exact_authority_values(self):
        original = self.path.read_bytes()
        plan = self.plan()
        self.assertEqual(self.path.read_bytes(), original)
        values = {change['cell']: change['new'] for change in plan['changes']}
        self.assertEqual(values['D5'], 4)
        self.assertEqual(values['F5'], self.source.pictures['1A'])
        self.assertEqual(plan['target_sha256'], updater.sha(original))
        self.assertEqual(plan['spec']['protected_ranges'], ['A4:J4', 'J12'])

    def test_apply_retains_other_sheets_formulas_styles_first_data_row_and_all_unmapped_cells(self):
        plan = self.plan()
        original = parts(self.path)
        original_cells = {e.get('r'): ET.tostring(e) for e in ET.fromstring(original['xl/worksheets/sheet1.xml']).iter(f'{{{updater.NS}}}c')}
        result = updater.apply_update(plan)
        self.assertEqual(result['status'], 'saved')
        updated = parts(self.path)
        for name, contents in original.items():
            if name != 'xl/worksheets/sheet1.xml':
                self.assertEqual(updated[name], contents, name)
        target_cells = {change['cell'] for change in plan['changes']}
        actual_cells = {e.get('r'): e for e in ET.fromstring(updated['xl/worksheets/sheet1.xml']).iter(f'{{{updater.NS}}}c')}
        for address, raw in original_cells.items():
            if address not in target_cells:
                self.assertEqual(ET.tostring(actual_cells[address]), raw, address)
            else:
                self.assertEqual(actual_cells[address].get('s'), ET.fromstring(raw).get('s'))
        # Replace just cell spans with placeholders: every other sheet byte must match.
        def masked(data):
            for span in sorted(updater._spans(data), key=lambda item: item['start'], reverse=True):
                if span['name'] == updater.NS + '}c' and span['attrs']['r'] in target_cells:
                    data = data[:span['start']] + b'<VALUE/>' + data[span['end']:]
            return data
        self.assertEqual(masked(updated['xl/worksheets/sheet1.xml']), masked(original['xl/worksheets/sheet1.xml']))
        with zipfile.ZipFile(self.path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.comment, b'original archive comment')
        self.assertEqual(updater.Book(self.path.read_bytes(), '目标表').value('F6'), self.source.pictures['1B'])

    def test_partial_column_update_preserves_other_authority_columns(self):
        original = updater.Book(self.path.read_bytes(), '目标表')
        plan = self.plan(columns={'镜号': 'C', '画面内容': 'F'})
        self.assertEqual({c['cell'] for c in plan['changes']}, {'F5', 'F6'})
        updater.apply_update(plan)
        updated = updater.Book(self.path.read_bytes(), '目标表')
        for address in ('D5', 'E5', 'G5', 'H5', 'A5', 'I5'):
            self.assertEqual(updated.value(address), original.value(address))

    def test_first_data_row_can_be_explicitly_protected(self):
        self.rejects_without_write('保护范围', start_row=4, writable_range='B4:H8', expected_existing_shots=['FIRST-KEEP', '1A'])
        self.rejects_without_write('首行|表头', header_row=1, start_row=1)

    def test_growth_only_uses_reserved_empty_cells_without_inserting_rows(self):
        make_workbook(self.path, reserved=True)
        plan = self.plan(expected_existing_shots=['1A'])
        self.assertEqual((plan['old_row_count'], plan['new_row_count']), (1, 2))
        updater.apply_update(plan)
        book = updater.Book(self.path.read_bytes(), '目标表')
        self.assertEqual(book.value('C6'), '1B')
        self.assertEqual(book.value('C7'), 'LATER-KEEP')
        self.assertEqual(book.value('I6'), '保留侧栏')
        self.assertEqual(book.rows[6].get('ht'), '68')

    def test_growth_can_fill_missing_cells_within_reserved_sheet_extent(self):
        make_workbook(self.path, reserved=True)
        def remove_blank_cells(text):
            import re
            return re.sub(r'<c r="[B-H]6" s="[12]"/>', '', text)
        mutate_sheet(self.path, remove_blank_cells)
        updater.apply_update(self.plan(expected_existing_shots=['1A']))
        book = updater.Book(self.path.read_bytes(), '目标表')
        self.assertEqual(book.value('C6'), '1B')
        self.assertEqual(book.value('I6'), '保留侧栏')

    def test_growth_can_fill_a_missing_row_within_existing_extent(self):
        import re
        mutate_sheet(self.path, lambda s: re.sub(r'<row r="6".*?</row>', '', s))
        updater.apply_update(self.plan(expected_existing_shots=['1A']))
        book = updater.Book(self.path.read_bytes(), '目标表')
        self.assertEqual(book.value('C6'), '1B')
        self.assertEqual(book.value('C7'), 'LATER-KEEP')
        self.assertEqual(list(book.rows), [1, 3, 4, 5, 6, 7, 12])

    def test_growth_cannot_expand_worksheet_dimension(self):
        make_workbook(self.path, reserved=True)
        mutate_sheet(self.path, lambda s: s.replace('dimension ref="A1:J12"', 'dimension ref="A1:J5"'))
        self.rejects_without_write('超出现有工作表范围', expected_existing_shots=['1A'])

    def test_shrink_requires_explicit_tail_clear_and_leaves_following_rows(self):
        self.rejects_without_write('clear_tail', shots=['1A'])
        plan = self.plan(shots=['1A'], clear_tail=True)
        self.assertEqual({c['cell'] for c in plan['changes'] if c['action'] == 'clear'}, {f'{col}6' for col in 'BCDEFGH'})
        updater.apply_update(plan)
        book = updater.Book(self.path.read_bytes(), '目标表')
        self.assertTrue(all(book.value(f'{col}6') is None for col in 'BCDEFGH'))
        self.assertEqual(book.value('I6'), '保留侧栏')
        self.assertEqual(book.value('C7'), 'LATER-KEEP')
        self.assertEqual(book.cells['D6'].get('s'), '2')

    def test_growth_and_shrink_require_full_authority_columns(self):
        self.rejects_without_write('全部列', shots=['1A'], clear_tail=True, columns={'镜号': 'C', '画面内容': 'F'})
        self.rejects_without_write('全部列', expected_existing_shots=['1A'], columns={'镜号': 'C', '画面内容': 'F'})

    def test_occupied_growth_range_and_unbounded_or_outside_ranges_reject(self):
        self.rejects_without_write('已有数据', expected_existing_shots=['1A'])
        for bound in ('B:H', 'B5:H5', 'H5:B10', 'B0:H10', 'B5:XFE10'):
            with self.subTest(bound=bound):
                self.rejects_without_write('范围|列|地址|区域', writable_range=bound)

    def test_wrong_sheet_header_original_ids_and_source_ids_reject(self):
        self.rejects_without_write('工作表', sheet='猜测工作表')
        self.rejects_without_write('表头', columns={'镜号': 'B', '画面内容': 'F'})
        self.rejects_without_write('原镜号', expected_existing_shots=['1Z', '1B'])
        self.rejects_without_write('精确镜号', shots=['1A', '1Z'])
        self.rejects_without_write('权威 Markdown', shots=['1B', '1A'])

    def test_formula_in_authorized_range_is_never_overwritten(self):
        mutate_sheet(self.path, lambda s: s.replace(cell('F5', '原F5'), cell('F5', None, formula='D5+D6')))
        self.rejects_without_write('公式')

    def test_array_formula_range_and_merged_cells_reject(self):
        mutate_sheet(self.path, lambda s: s.replace('<f>D5+D6</f>', '<f t="array" ref="F5:F6">D5+D6</f>'))
        self.rejects_without_write('公式范围')
        make_workbook(self.path)
        mutate_sheet(self.path, lambda s: s.replace('</mergeCells>', '<mergeCell ref="F5:G5"/></mergeCells>'))
        self.rejects_without_write('合并')

    def test_rich_text_and_protected_sheet_reject(self):
        mutate_sheet(self.path, lambda s: s.replace(cell('F5', '原F5'), '<c r="F5" s="1" t="inlineStr"><is><r><rPr><b/></rPr><t>富文本</t></r></is></c>'))
        self.rejects_without_write('富文本')
        make_workbook(self.path)
        mutate_sheet(self.path, lambda s: s.replace('</sheetData>', '</sheetData><sheetProtection sheet="1"/>'))
        self.rejects_without_write('受保护')

    def test_structured_table_values_can_update_but_resizing_is_rejected(self):
        contents = parts(self.path)
        contents['xl/worksheets/sheet1.xml'] = contents['xl/worksheets/sheet1.xml'].replace(
            b'</worksheet>', b'<tableParts count="1"><tablePart r:id="table1"/></tableParts></worksheet>')
        contents['xl/worksheets/_rels/sheet1.xml.rels'] = (
            f'<Relationships xmlns="{updater.PKG_NS}"><Relationship Id="table1" Type="{updater.REL_NS}/table" Target="../tables/table1.xml"/></Relationships>').encode()
        contents['xl/tables/table1.xml'] = (
            f'<table xmlns="{updater.NS}" id="1" name="Shots" displayName="Shots" ref="B3:H6" headerRowCount="1"><tableColumns count="7">'
            + ''.join(f'<tableColumn id="{i + 1}" name="{header}"/>' for i, header in enumerate(updater.HEADERS)) + '</tableColumns></table>').encode()
        contents['[Content_Types].xml'] = contents['[Content_Types].xml'].replace(b'</Types>', b'<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/></Types>')
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, value in contents.items():
                archive.writestr(name, value)
        original_table = contents['xl/tables/table1.xml']
        self.rejects_without_write('结构化 Excel 表', shots=['1A'], clear_tail=True)
        updater.apply_update(self.plan())
        self.assertEqual(parts(self.path)['xl/tables/table1.xml'], original_table)

    def test_ambiguous_xml_cell_position_is_rejected(self):
        mutate_sheet(self.path, lambda s: s.replace('</worksheet>', cell('F5', '伪造的第二个目标') + '</worksheet>'))
        self.rejects_without_write('XML 位置')

    def test_a_zip_disguised_as_xlsx_without_package_identity_is_rejected(self):
        contents = parts(self.path)
        contents.pop('[Content_Types].xml')
        with zipfile.ZipFile(self.path, 'w') as archive:
            for name, value in contents.items():
                archive.writestr(name, value)
        self.rejects_without_write('缺失工作簿')

    def test_source_validation_runs_before_preview_and_again_before_apply(self):
        plan = self.plan()
        before = self.path.read_bytes()
        (self.root / 'source.md').write_text('权威源已换版')
        with self.assertRaisesRegex(updater.UpdateError, '校验未通过'):
            updater.apply_update(plan)
        self.assertEqual(self.path.read_bytes(), before)
        self.rejects_without_write('校验未通过')

    def test_stale_target_and_tampered_plan_never_write(self):
        plan = self.plan()
        mutate_sheet(self.path, lambda s: s.replace('用户批注5', '其他人刚修改的反馈'))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(updater.UpdateError, '已改变'):
            updater.apply_update(plan)
        self.assertEqual(self.path.read_bytes(), before)
        plan = self.plan()
        plan['changes'][0]['new'] = '不能通过篡改预览绕过权威稿'
        with self.assertRaisesRegex(updater.UpdateError, '已改变'):
            updater.apply_update(plan)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_change_during_preparation_is_detected(self):
        plan = self.plan()
        original_build = updater._build_bytes
        observed = []
        def race(*args):
            result = original_build(*args)
            mutate_sheet(self.path, lambda s: s.replace('用户批注5', '并发修改应保留'))
            observed.append(self.path.read_bytes())
            return result
        with patch.object(updater, '_build_bytes', side_effect=race), self.assertRaisesRegex(updater.UpdateError, '变化'):
            updater.apply_update(plan)
        self.assertEqual(self.path.read_bytes(), observed[0])
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_failed_atomic_replace_leaves_original_and_cleans_temporary(self):
        plan = self.plan()
        original = self.path.read_bytes()
        with patch.object(updater.os, 'replace', side_effect=OSError('模拟文件替换失败')), self.assertRaisesRegex(OSError, '模拟'):
            updater.apply_update(plan)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_excel_editor_lock_and_symlink_are_rejected(self):
        lock = self.path.with_name('~$' + self.path.name)
        lock.write_bytes(b'lock')
        self.rejects_without_write('编辑锁')
        lock.unlink()
        link = self.root / 'alias.xlsx'
        link.symlink_to(self.path)
        self.rejects_without_write('符号链接', workbook=str(link))

    def test_stage3_five_columns_use_current_full_validator_and_readable_newlines(self):
        make_workbook(self.path, stage3=True)
        spec = copy.deepcopy(self.spec)
        spec.update(columns=dict(zip(updater.STAGE3_HEADERS, 'BCDEF')), writable_range='B5:F10')
        plan = updater.plan_update(self.source.s3, spec)
        updater.apply_update(plan)
        book = updater.Book(self.path.read_bytes(), '目标表')
        self.assertEqual(book.value('B5'), '1A')
        self.assertEqual(book.value('C5'), '片段1')
        self.assertEqual(book.value('D5'), 4)
        self.assertIn('\n\n画面内容：', book.value('E5'))
        self.assertNotIn('<br>', book.value('F5'))

    def test_cli_preview_then_apply_with_target_and_plan_binding(self):
        spec_path = self.root / 'update.json'
        spec_path.write_text(json.dumps(self.spec, ensure_ascii=False))
        command = [sys.executable, str(ROOT / 'scripts' / 'update_workbook.py'), '--input', str(self.source.s1), '--spec', str(spec_path)]
        original = self.path.read_bytes()
        preview = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(self.path.read_bytes(), original)
        plan = json.loads(preview.stdout)
        bad = subprocess.run(command + ['--apply'], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(self.path.read_bytes(), original)
        changed_plan = subprocess.run(command + ['--apply', '--expected-target-sha256', plan['target_sha256'], '--expected-plan-sha256', '0' * 64], capture_output=True, text=True)
        self.assertEqual(changed_plan.returncode, 1)
        self.assertEqual(self.path.read_bytes(), original)
        applied = subprocess.run(command + ['--apply', '--expected-target-sha256', plan['target_sha256'], '--expected-plan-sha256', plan['plan_sha256']], capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['readback'], 'passed')

    def test_cli_relative_workbook_belongs_to_spec_and_not_working_directory(self):
        elsewhere = self.root / 'different-working-directory'
        elsewhere.mkdir()
        wrong_target = elsewhere / self.path.name
        make_workbook(wrong_target)
        wrong_bytes = wrong_target.read_bytes()
        spec = copy.deepcopy(self.spec)
        spec['workbook'] = self.path.name
        spec_path = self.root / 'relative-update.json'
        spec_path.write_text(json.dumps(spec, ensure_ascii=False))
        command = [sys.executable, '-B', str(ROOT / 'scripts/update_workbook.py'),
                   '--input', str(self.source.s1), '--spec', str(spec_path)]
        preview = subprocess.run(command, cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        plan = json.loads(preview.stdout)
        self.assertEqual(Path(plan['spec']['workbook']).resolve(), self.path.resolve())
        applied = subprocess.run(command + ['--apply', '--expected-target-sha256', plan['target_sha256'],
                                           '--expected-plan-sha256', plan['plan_sha256']],
                                 cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(updater.Book(self.path.read_bytes(), '目标表').value('F5'), self.source.pictures['1A'])
        self.assertEqual(wrong_target.read_bytes(), wrong_bytes)
        # Resolving a relative path must not hide the final symlink component.
        link = self.root / 'linked.xlsx'
        link.symlink_to(self.path)
        spec['workbook'] = link.name
        spec_path.write_text(json.dumps(spec, ensure_ascii=False))
        rejected = subprocess.run(command, cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn('符号链接', rejected.stderr)

    @unittest.skipUnless(importlib.util.find_spec('openpyxl'), '本地可选独立 XLSX 读取器 openpyxl 未安装')
    def test_independent_reader_reopens_real_xlsx_with_values_formulas_and_format(self):
        import openpyxl
        before = openpyxl.load_workbook(self.path)
        style = copy.copy(before['目标表']['F5']._style)
        first = before['目标表']['C4'].value
        updater.apply_update(self.plan())
        after = openpyxl.load_workbook(self.path)
        self.assertEqual(after.sheetnames, ['目标表', '其他表'])
        self.assertEqual(after['目标表']['F5'].value, self.source.pictures['1A'])
        self.assertEqual(after['目标表']['F5']._style, style)
        self.assertEqual(after['目标表']['D5'].value, 4)
        self.assertEqual(after['目标表']['D5'].number_format, '0.0"s"')
        self.assertEqual(after['目标表']['I5'].value, '=D5+D6')
        self.assertEqual(after['其他表']['B2'].value, "='目标表'!D5+'目标表'!D6")
        self.assertEqual(after['目标表']['C4'].value, first)
        self.assertEqual(after['目标表'].row_dimensions[5].height, 68)
        self.assertEqual(after['目标表'].freeze_panes, 'A4')
        before.close()
        after.close()


if __name__ == '__main__':
    unittest.main()
