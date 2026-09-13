#!/usr/bin/env python3
"""Preview or apply bounded value updates to an explicitly selected existing XLSX.

No worksheet is inferred and no rows are inserted, deleted, or restyled. A JSON
spec names workbook, sheet, header_row, start_row, writable_range, columns
(Markdown header -> Excel column), shots, expected_existing_shots, and optional
protected_ranges / clear_tail. Sheet row 1 and the specified header are always
protected. Additional protection can include the first *data* row.

Preview: update_workbook.py --input stage1.md --spec update.json
Apply:   same arguments + --apply --expected-target-sha256 <preview hash>
An optional --expected-plan-sha256 also binds a previously reviewed source/spec.
Actual writes are for the caller's existing user authorization, not a new
approval mechanism. The Python apply_update(plan) API always binds that plan.

This intentionally small OOXML editor supports ordinary, unencrypted .xlsx
data cells in an existing worksheet extent. It retains cell styles, sheet XML
outside changed cells, and every other ZIP member's uncompressed bytes. It
rejects protected sheets, target formulas/rich text/merged cells/cell metadata,
signed or macro files, ambiguous XML, and structural-table resizing. Existing
blank cell slots must be reserved before growing past the worksheet extent.
Formula text and cached results are retained; formula/chart recalculation and
native Excel rendering are outside this tool's acceptance claim.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from xml.parsers import expat
import zipfile

from shotlist_common import HEADERS, find_tables, parse_duration, parse_frontmatter

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
MAX_ROW, MAX_COL = 1048576, 16384
MAX_UPDATE_CELLS = 100000
STAGE3_HEADERS = ('镜号', '片段', '时长', '分镜内容', 'Seedance提示词')


class UpdateError(ValueError):
    """A rejected update: the target has not been written."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def column_number(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{1,3}', value):
        raise UpdateError(f'列必须使用大写 Excel 列字母：{value!r}')
    number = 0
    for char in value:
        number = number * 26 + ord(char) - 64
    if number > MAX_COL:
        raise UpdateError('Excel 列超出 XFD')
    return number


def cell_position(value: str) -> tuple[int, int]:
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]*)', value) if isinstance(value, str) else None
    if not match or int(match[2]) > MAX_ROW:
        raise UpdateError(f'必须使用明确的 A1 单元格地址：{value!r}')
    return column_number(match[1]), int(match[2])


def region(value: str) -> tuple[int, int, int, int]:
    parts = value.split(':') if isinstance(value, str) else []
    if len(parts) not in (1, 2):
        raise UpdateError(f'必须使用有限 A1 范围，不能使用整行/整列：{value!r}')
    left, top = cell_position(parts[0])
    right, bottom = cell_position(parts[-1])
    if left > right or top > bottom:
        raise UpdateError(f'范围倒置：{value}')
    return left, top, right, bottom


def inside(address: str, bounds: tuple[int, int, int, int]) -> bool:
    column, row = cell_position(address)
    return bounds[0] <= column <= bounds[2] and bounds[1] <= row <= bounds[3]


def parse_xml(data: bytes) -> ET.Element:
    if re.search(br'<!DOCTYPE|<!ENTITY', data, re.I):
        raise UpdateError('不支持含 DTD 或实体声明的 XML')
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise UpdateError(f'无效 OOXML：{exc}') from exc


def _relationships(parts: dict[str, bytes], owner: str) -> dict[str, tuple[str, str]]:
    directory, name = posixpath.split(owner)
    rel_path = posixpath.join(directory, '_rels', name + '.rels')
    root = parse_xml(parts.get(rel_path, b'<Relationships/>'))
    result = {}
    for rel in root:
        if rel.tag != f'{{{PKG_NS}}}Relationship':
            raise UpdateError(f'不支持的关系部件：{rel_path}')
        rid, target = rel.get('Id'), rel.get('Target', '')
        if not rid or rid in result or rel.get('TargetMode') == 'External':
            raise UpdateError(f'关系目标不唯一或为外部目标：{rel_path}')
        resolved = posixpath.normpath(posixpath.join(directory, target)) if not target.startswith('/') else target[1:]
        if resolved.startswith('../') or resolved not in parts:
            raise UpdateError(f'关系部件不存在：{resolved}')
        result[rid] = (resolved, rel.get('Type', ''))
    return result


class Book:
    def __init__(self, data: bytes, sheet: str):
        self.sheet = sheet
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self.infos = archive.infolist()
                self.comment = archive.comment
                names = [info.filename for info in self.infos]
                if len(names) != len(set(names)) or any(info.flag_bits & 1 for info in self.infos):
                    raise UpdateError('工作簿含重复或加密 ZIP 部件')
                if any(name.startswith('_xmlsignatures/') or 'vbaproject' in name.lower() for name in names):
                    raise UpdateError('不支持带数字签名或宏的工作簿')
                self.parts = {name: archive.read(name) for name in names}
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise UpdateError(f'无法读取普通 .xlsx 文件：{exc}') from exc
        try:
            types = parse_xml(self.parts['[Content_Types].xml'])
            workbook_types = [entry.get('ContentType') for entry in types if entry.get('PartName') == '/xl/workbook.xml']
            if workbook_types != ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml']:
                raise UpdateError('目标不是普通 .xlsx 工作簿，或工作簿类型声明不唯一')
            package_rels = parse_xml(self.parts['_rels/.rels'])
            office = [entry for entry in package_rels if entry.get('Type', '').endswith('/officeDocument')]
            if (len(office) != 1 or office[0].get('Target', '').lstrip('/') != 'xl/workbook.xml'
                    or office[0].get('TargetMode') == 'External'):
                raise UpdateError('工作簿入口关系不明确或不受支持')
            workbook = parse_xml(self.parts['xl/workbook.xml'])
            rels = _relationships(self.parts, 'xl/workbook.xml')
            sheets = [s for s in workbook.findall(f'{{{NS}}}sheets/{{{NS}}}sheet') if s.get('name') == sheet]
            if len(sheets) != 1:
                raise UpdateError(f'工作表名称必须精确且唯一：{sheet!r}')
            self.part, kind = rels[sheets[0].get(f'{{{REL_NS}}}id')]
            if not kind.endswith('/worksheet'):
                raise UpdateError('指定对象不是普通工作表')
            self.raw = self.parts[self.part]
            self.root = parse_xml(self.raw)
        except KeyError as exc:
            raise UpdateError(f'缺失工作簿关系或部件：{exc}') from exc
        if self.root.tag != f'{{{NS}}}worksheet':
            raise UpdateError('仅支持标准 Transitional OOXML 工作表')
        if len(self.root.findall(f'{{{NS}}}sheetData')) != 1:
            raise UpdateError('工作表必须且只能有一个明确的 sheetData 区域')
        protection = self.root.find(f'{{{NS}}}sheetProtection')
        if protection is not None and protection.get('sheet', '1') not in ('0', 'false'):
            raise UpdateError('工作表受保护；不会绕过工作簿保护')
        if self.root.find(f'{{{NS}}}extLst') is not None:
            raise UpdateError('工作表含扩展数据，无法保证定点更新保真')
        dimension = self.root.find(f'{{{NS}}}dimension')
        self.extent = region(dimension.get('ref')) if dimension is not None else None
        self.cells = {}
        self.rows = {}
        for row in self.root.findall(f'{{{NS}}}sheetData/{{{NS}}}row'):
            row_num = int(row.get('r', '0'))
            if not 1 <= row_num <= MAX_ROW or row_num in self.rows:
                raise UpdateError('工作表行号缺失或重复')
            self.rows[row_num] = row
            last_column = 0
            for cell in row:
                if cell.tag != f'{{{NS}}}c':
                    raise UpdateError('工作表数据行含不支持的扩展元素')
                address = cell.get('r', '')
                col, number = cell_position(address)
                if number != row_num or address in self.cells or col <= last_column:
                    raise UpdateError('工作表单元格位置不唯一或未排序')
                self.cells[address] = cell
                last_column = col
        if list(self.rows) != sorted(self.rows):
            raise UpdateError('工作表行必须按行号排序')
        if (len(list(self.root.iter(f'{{{NS}}}c'))) != len(self.cells)
                or len(list(self.root.iter(f'{{{NS}}}row'))) != len(self.rows)):
            raise UpdateError('行或单元格出现在不支持的 XML 位置，无法保证保真')
        self.shared = []
        for path, kind in rels.values():
            if kind.endswith('/sharedStrings'):
                self.shared = list(parse_xml(self.parts[path]))
        self.merges = [region(e.get('ref')) for e in self.root.findall(f'{{{NS}}}mergeCells/{{{NS}}}mergeCell')]
        self.formula_ranges = [region(f.get('ref')) for f in self.root.iter(f'{{{NS}}}f') if f.get('ref')]
        self.tables = []
        table_parts = self.root.findall(f'{{{NS}}}tableParts/{{{NS}}}tablePart')
        if table_parts:
            sheet_rels = _relationships(self.parts, self.part)
            for entry in table_parts:
                target, kind = sheet_rels[entry.get(f'{{{REL_NS}}}id')]
                if not kind.endswith('/table'):
                    raise UpdateError('非法结构化表关系')
                self.tables.append(parse_xml(self.parts[target]))

    def value(self, address: str):
        cell = self.cells.get(address)
        if cell is None:
            return None
        kind = cell.get('t', 'n')
        value = cell.find(f'{{{NS}}}v')
        if kind == 'inlineStr':
            return ''.join(t.text or '' for t in cell.findall(f'{{{NS}}}is//{{{NS}}}t'))
        if value is None or value.text is None:
            return None
        if kind == 's':
            try:
                index = int(value.text)
                if index < 0:
                    raise ValueError('negative index')
                return ''.join(t.text or '' for t in self.shared[index].iter(f'{{{NS}}}t'))
            except (ValueError, IndexError) as exc:
                raise UpdateError(f'{address} 共享字符串无效') from exc
        if kind == 'n':
            try:
                from decimal import Decimal
                number = Decimal(value.text)
                if not number.is_finite():
                    raise ValueError('not finite')
                return int(number) if number == int(number) else float(number)
            except (ValueError, ArithmeticError) as exc:
                raise UpdateError(f'{address} 数字无效') from exc
        if kind == 'b':
            return value.text == '1'
        return value.text

    def check_editable(self, address: str):
        if self.extent is None or not inside(address, self.extent):
            raise UpdateError(f'{address} 超出现有工作表范围；请先明确预留数据区域，工具不扩表')
        if any(inside(address, bounds) for bounds in self.merges + self.formula_ranges):
            raise UpdateError(f'{address} 位于合并单元格或数组/共享公式范围')
        cell = self.cells.get(address)
        if cell is None:
            return
        if cell.find(f'{{{NS}}}f') is not None:
            raise UpdateError(f'{address} 是公式，不能覆盖或清除')
        if set(cell.attrib) - {'r', 's', 't'} or any(child.tag not in (f'{{{NS}}}v', f'{{{NS}}}is') for child in cell):
            raise UpdateError(f'{address} 含无法保真的单元格元数据')
        if cell.get('t') not in (None, 'n', 's', 'inlineStr', 'str', 'b', 'e', 'd'):
            raise UpdateError(f'{address} 单元格类型不受支持')
        rich = cell.find(f'{{{NS}}}is')
        if cell.get('t') == 's':
            raw = cell.find(f'{{{NS}}}v')
            try:
                index = int(raw.text)
                if index < 0:
                    raise ValueError('negative index')
                rich = self.shared[index]
            except (AttributeError, ValueError, IndexError) as exc:
                raise UpdateError(f'{address} 共享字符串无效') from exc
        if rich is not None and any(child.tag != f'{{{NS}}}t' for child in rich):
            raise UpdateError(f'{address} 含富文本或注音，不能丢失其局部格式')


def _validated_rows(path: Path):
    from generation_tasks import _InputSnapshot
    try:
        snapshot = _InputSnapshot(path, kind='authority')
    except (ValueError, OSError, UnicodeError) as exc:
        raise UpdateError('当前 V2 输入/来源校验无法读取：' + str(exc)) from exc
    text = snapshot.texts[path.resolve()]
    initial_digest = snapshot.record(path)['sha256']
    front = parse_frontmatter(text)
    if front.errors:
        raise UpdateError('\n'.join(front.errors))
    artifact = front.values.get('artifact')
    if artifact == 'horizontal-tv-shotlist':
        errors = importlib.import_module('validate_v2_shotlist').validate_file(path)
        headers = HEADERS
        rows = [row for table in find_tables(text) if table.headers == headers for row in table.rows]
    elif artifact == 'horizontal-tv-stage3-v2':
        validator = importlib.import_module('validate_v2_prompts')
        errors = validator.validate_stage3(path)
        headers, rows, parse_errors = validator.stage3_table(text)
        errors += parse_errors
    else:
        raise UpdateError('输入必须为当前 V2 Stage 1 七列或 Stage 3 五列/条件六列权威 Markdown')
    if errors:
        raise UpdateError('当前 V2 输入/来源校验未通过：\n' + '\n'.join(errors))
    try:
        snapshot.verify_unchanged()
    except ValueError as exc:
        raise UpdateError('权威 Markdown 或依赖在校验期间发生变化：' + str(exc)) from exc
    by_id = {}
    id_index = headers.index('镜号')
    for row in rows:
        if len(row) != len(headers) or row[id_index] in by_id:
            raise UpdateError('输入镜号重复或列数不符')
        values = {}
        for header, raw in zip(headers, row):
            if header == '时长':
                duration = parse_duration(raw)
                value = int(duration) if duration == int(duration) else float(duration)
            else:
                value = re.sub(r'<br\s*/?>', '\n', raw, flags=re.I) if artifact == 'horizontal-tv-stage3-v2' else raw
            if isinstance(value, str):
                if len(value.encode('utf-16-le')) // 2 > 32767 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
                    raise UpdateError(f'镜号 {row[id_index]} 的 {header} 超出 Excel 文本限制')
            values[header] = value
        by_id[row[id_index]] = values
    return tuple(headers), by_id, initial_digest, snapshot


def _spec(spec: dict) -> dict:
    spec = copy.deepcopy(spec)
    required = {'workbook', 'sheet', 'header_row', 'start_row', 'writable_range', 'columns', 'shots', 'expected_existing_shots'}
    optional = {'protected_ranges', 'clear_tail'}
    if not isinstance(spec, dict) or required - spec.keys() or set(spec) - required - optional:
        raise UpdateError(f'spec 必须明确且只使用以下字段：{sorted(required | optional)}')
    for key in ('header_row', 'start_row'):
        if type(spec[key]) is not int or not 1 <= spec[key] <= MAX_ROW:
            raise UpdateError(f'{key} 必须为有效一基行号')
    if not spec['header_row'] < spec['start_row'] or spec['start_row'] == 1:
        raise UpdateError('只支持表头之后的数据区域；首行及表头不能写入')
    if not isinstance(spec['sheet'], str) or not spec['sheet']:
        raise UpdateError('sheet 必须为完整工作表名')
    if not isinstance(spec['workbook'], str) or not Path(spec['workbook']).is_absolute():
        raise UpdateError('workbook 必须为用户明确指定的绝对路径')
    if not isinstance(spec['columns'], dict) or '镜号' not in spec['columns']:
        raise UpdateError('columns 必须显式映射镜号列及本次要更新的列')
    for col in spec['columns'].values():
        column_number(col)
    if len(set(spec['columns'].values())) != len(spec['columns']):
        raise UpdateError('columns 不得映射到重复的目标列')
    for key in ('shots', 'expected_existing_shots'):
        values = spec[key]
        if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values) or len(values) != len(set(values)):
            raise UpdateError(f'{key} 必须为不重复的精确镜号数组')
    if not spec['shots'] and not spec['expected_existing_shots']:
        raise UpdateError('更新范围为空')
    if max(len(spec['shots']), len(spec['expected_existing_shots'])) * len(spec['columns']) > MAX_UPDATE_CELLS:
        raise UpdateError('本工具仅支持一次不超过 100000 个单元格的局部更新')
    region(spec['writable_range'])
    spec.setdefault('protected_ranges', [])
    if not isinstance(spec['protected_ranges'], list):
        raise UpdateError('protected_ranges 必须为有限 A1 范围数组')
    for bounds in spec['protected_ranges']:
        region(bounds)
    spec.setdefault('clear_tail', False)
    if type(spec['clear_tail']) is not bool:
        raise UpdateError('clear_tail 必须为布尔值')
    return spec


def _target(path: Path) -> tuple[bytes, tuple]:
    if path.suffix.lower() != '.xlsx' or path.is_symlink():
        raise UpdateError('目标必须是明确的普通 .xlsx 文件，不能是符号链接')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UpdateError('目标必须为单一普通文件，不能有硬链接')
    if path.with_name('~$' + path.name).exists():
        raise UpdateError('目标存在 Excel 编辑锁，请在完成当前编辑后重试')
    data = path.read_bytes()
    after = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(info) != identity(after):
        raise UpdateError('目标在读取期间发生变化')
    return data, identity(after)


def plan_update(input_path: Path, spec: dict) -> dict:
    """Validate complete V2 input, then return a JSON-serializable cell diff."""
    spec = _spec(spec)
    input_path = Path(input_path).resolve(strict=True)
    headers, source_rows, input_digest, snapshot = _validated_rows(input_path)
    if not set(spec['columns']) <= set(headers):
        raise UpdateError('columns 包含不属于当前权威表的列名')
    missing = set(spec['shots']) - source_rows.keys()
    if missing:
        raise UpdateError(f'源文件没有精确镜号：{sorted(missing)}')
    source_order = [shot for shot in source_rows if shot in spec['shots']]
    if source_order != spec['shots']:
        raise UpdateError('shots 必须保持权威 Markdown 的镜号顺序')
    path = Path(spec['workbook'])
    original, _ = _target(path)
    spec['workbook'] = str(path.resolve())
    book = Book(original, spec['sheet'])
    for header, column in spec['columns'].items():
        address = f'{column}{spec["header_row"]}'
        if book.value(address) != header or book.cells.get(address).find(f'{{{NS}}}f') is not None:
            raise UpdateError(f'目标表头不匹配：{address} 应为 {header!r}')
    old_count, new_count = len(spec['expected_existing_shots']), len(spec['shots'])
    if old_count != new_count and set(spec['columns']) != set(headers):
        raise UpdateError('行数增长/缩减必须映射当前权威表的全部列，避免半行新旧混合')
    if old_count > new_count and not spec['clear_tail']:
        raise UpdateError('数据缩短必须明确 clear_tail:true；只清 expected_existing_shots 对应的旧尾行')
    start = spec['start_row']
    allowed = region(spec['writable_range'])
    protected = [region(item) for item in spec['protected_ranges']]
    id_column = spec['columns']['镜号']
    for offset, expected in enumerate(spec['expected_existing_shots']):
        address = f'{id_column}{start + offset}'
        if book.value(address) != expected:
            raise UpdateError(f'错误目标或原镜号已变：{address} 预期 {expected!r}，实际 {book.value(address)!r}')
    changes, touched = [], []
    for offset in range(max(old_count, new_count)):
        row_number = start + offset
        for header, column in spec['columns'].items():
            address = f'{column}{row_number}'
            if row_number > MAX_ROW or not inside(address, allowed):
                raise UpdateError(f'{address} 超出明确授权的可写区域')
            if row_number in (1, spec['header_row']) or any(inside(address, bounds) for bounds in protected):
                raise UpdateError(f'{address} 属于保护范围，包含实际首条分镜时也不能覆盖')
            book.check_editable(address)
            previous = book.value(address)
            if offset >= old_count and previous not in (None, ''):
                raise UpdateError(f'增长位置 {address} 已有数据，不能覆盖未列入原镜号的内容')
            value = source_rows[spec['shots'][offset]][header] if offset < new_count else None
            touched.append(address)
            if previous != value or (type(previous) is bool and type(value) is not bool):
                changes.append({'cell': address, 'column': header, 'shot': spec['shots'][offset] if offset < new_count else None,
                                'old': previous, 'new': value, 'action': 'clear' if offset >= new_count else 'set'})
    for table in book.tables:
        bounds = region(table.get('ref'))
        intersections = [address for address in touched if inside(address, bounds)]
        if not intersections:
            continue
        if old_count != new_count:
            raise UpdateError('不支持结构化 Excel 表中的行数增长/缩减；不会暗改表边界或汇总范围')
        header_end = bounds[1] + int(table.get('headerRowCount', '1'))
        data_end = bounds[3] - int(table.get('totalsRowCount', '0'))
        for address in intersections:
            col, number = cell_position(address)
            if not header_end <= number <= data_end:
                raise UpdateError(f'{address} 位于结构化表头或汇总行')
            columns = table.findall(f'{{{NS}}}tableColumns/{{{NS}}}tableColumn')
            if col - bounds[0] < len(columns) and columns[col - bounds[0]].find(f'{{{NS}}}calculatedColumnFormula') is not None:
                raise UpdateError(f'{address} 属于结构化表公式列')
    plan = {'format': 'horizontal-tv-workbook-update-v1', 'input': str(input_path), 'input_sha256': input_digest,
            'target_sha256': sha(original), 'spec': spec, 'old_row_count': old_count, 'new_row_count': new_count,
            'changes': changes, 'changed_cells': len(changes),
            'limits': ['不插删行或改样式；其他工作表及非目标单元格保持原内容',
                       '公式和图表缓存保留，计算结果及原生 Excel 显示未在此校验']}
    try:
        snapshot.verify_unchanged()
    except ValueError as exc:
        raise UpdateError('权威来源在预览期间发生变化：' + str(exc)) from exc
    plan['input_dependencies'] = [snapshot.record(logical) | {'logical_path': str(logical),
                                  'state': list(entry['state']), 'roles': sorted(snapshot.roles[entry['path']])}
                                  for logical, entry in sorted(snapshot.entries.items())]
    from shot_revision import numbering_errors
    authority = input_path
    front = parse_frontmatter(snapshot.texts[input_path]).values
    if front.get('artifact') == 'horizontal-tv-stage3-v2':
        authority = (input_path.parent / front['source_stage1']).resolve()
    revision_errors, revision = numbering_errors(authority, snapshot.texts[authority])
    if revision_errors:
        raise UpdateError('修订映射无法进入工作簿预览：' + '\n'.join(revision_errors))
    if revision is not None and spec['expected_existing_shots']:
        # Updating a revision must not use an insertion to overwrite an
        # unrelated surviving old shot, or bring in an unmapped old identity.
        mapping = revision['mapping']
        expected_old = spec['expected_existing_shots']
        known_old = {entry['old'] for entry in mapping if entry['old'] is not None}
        selected_new = set(spec['shots'])
        if not set(expected_old) <= known_old:
            raise UpdateError('修订更新的原镜号必须存在于实际旧主序列映射；覆盖表请独立核对')
        for entry in mapping:
            if entry['old'] in expected_old and entry['new'] is not None and entry['new'] not in selected_new:
                raise UpdateError('修订更新不能丢弃目标区仍保留的旧镜；请扩大授权空位或缩小正确范围')
            if entry['new'] in selected_new and entry['old'] is not None and entry['old'] not in expected_old:
                raise UpdateError('修订更新不能以其他旧镜替换未对应的目标镜号')
    if revision is not None:
        plan['shot_revision'] = {'record': snapshot.record(Path(revision['path'])),
            'base_stage1': snapshot.record(Path(revision['base_stage1'])),
            'mode': revision['mode'], 'mapping': revision['mapping']}
    try:
        snapshot.verify_unchanged()
    except ValueError as exc:
        raise UpdateError('权威来源在预览期间发生变化：' + str(exc)) from exc
    plan['plan_sha256'] = sha(canonical(plan))
    return plan


def _spans(data: bytes):
    """Find lexical spans without reserializing XML or its namespace prefixes."""
    spans, stack = [], []
    parser = expat.ParserCreate(namespace_separator='}')

    def start(name, attrs):
        begin = parser.CurrentByteIndex
        match = re.match(br'<([^\s/>]+)(?:\s+(?:[^>"\']|"[^"]*"|\'[^\']*\')*)?/?>', data[begin:])
        if match is None:
            raise UpdateError('无法无损定位 XML 开始标签')
        end = begin + match.end()
        stack.append({'name': name, 'attrs': attrs, 'start': begin, 'open_end': end,
                      'rawname': match[1], 'empty': data[begin:end].rstrip().endswith(b'/>')})

    def end(name):
        item = stack.pop()
        item['end_start'] = item['open_end'] if item['empty'] else parser.CurrentByteIndex
        item['end'] = item['open_end'] if item['empty'] else data.index(b'>', parser.CurrentByteIndex) + 1
        spans.append(item)

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.Parse(data, True)
    return spans


def _cell_xml(raw_open: bytes, tag: bytes, value) -> bytes:
    opening = re.sub(br'\s+t\s*=\s*(?:"[^"]*"|\'[^\']*\')', b'', raw_open)
    opening = re.sub(br'/?>$', b'', opening)
    prefix = tag.rsplit(b':', 1)[0] + b':' if b':' in tag else b''
    if value is None:
        return opening + b'/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        contents = b'<' + prefix + b'v>' + str(value).encode() + b'</' + prefix + b'v>'
    else:
        opening += b' t="inlineStr"'
        text = escape(str(value)).replace('\r', '&#13;').encode('utf-8')
        contents = b'<' + prefix + b'is><' + prefix + b't xml:space="preserve">' + text + b'</' + prefix + b't></' + prefix + b'is>'
    return opening + b'>' + contents + b'</' + tag + b'>'


def _patched_sheet(book: Book, changes: list[dict]) -> bytes:
    spans = _spans(book.raw)
    cells = {span['attrs']['r']: span for span in spans if span['name'] == NS + '}c'}
    rows = {int(span['attrs']['r']): span for span in spans if span['name'] == NS + '}row'}
    data_span = next(span for span in spans if span['name'] == NS + '}sheetData')
    edits, missing = [], {}
    for change in changes:
        address, value = change['cell'], change['new']
        if address in cells:
            span = cells[address]
            edits.append((span['start'], span['end'], _cell_xml(book.raw[span['start']:span['open_end']], span['rawname'], value)))
        elif value is not None:
            missing.setdefault(cell_position(address)[1], []).append(change)
    for number, additions in missing.items():
        additions.sort(key=lambda change: cell_position(change['cell'])[0])
        row_span = rows.get(number)
        parent_tag = row_span['rawname'] if row_span else data_span['rawname']
        prefix = parent_tag.rsplit(b':', 1)[0] + b':' if b':' in parent_tag else b''
        new_cells = [(change, _cell_xml(b'<' + prefix + b'c r="' + change['cell'].encode() + b'">', prefix + b'c', change['new'])) for change in additions]
        if row_span and not row_span['empty']:
            existing = sorted((cell_position(addr)[0], span) for addr, span in cells.items() if cell_position(addr)[1] == number)
            for change, content in new_cells:
                position = next((span['start'] for col, span in existing if col > cell_position(change['cell'])[0]), row_span['end_start'])
                edits.append((position, position, content))
        elif row_span:
            opening = re.sub(br'/\s*>$', b'>', book.raw[row_span['start']:row_span['open_end']])
            content = opening + b''.join(cell for _, cell in new_cells) + b'</' + row_span['rawname'] + b'>'
            edits.append((row_span['start'], row_span['end'], content))
        else:
            position = next((rows[n]['start'] for n in sorted(rows) if n > number), data_span['end_start'])
            content = b'<' + prefix + b'row r="' + str(number).encode() + b'">' + b''.join(cell for _, cell in new_cells) + b'</' + prefix + b'row>'
            edits.append((position, position, content))
    grouped = {}
    for start, end, replacement in edits:
        grouped.setdefault((start, end), []).append(replacement)
    output, cursor = [], 0
    for (start, end), replacements in sorted(grouped.items()):
        if start < cursor:
            raise UpdateError('内部定点更新范围重叠')
        output.extend((book.raw[cursor:start], b''.join(replacements)))
        cursor = end
    output.append(book.raw[cursor:])
    return b''.join(output)


def _build_bytes(book: Book, changes: list[dict]) -> bytes:
    updated = _patched_sheet(book, changes)
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as output:
        output.comment = book.comment
        for info in book.infos:
            output.writestr(copy.copy(info), updated if info.filename == book.part else book.parts[info.filename])
    data = result.getvalue()
    readback = Book(data, book.sheet)
    for change in changes:
        if readback.value(change['cell']) != change['new']:
            raise UpdateError(f'临时工作簿回读不一致：{change["cell"]}')
    for name, original in book.parts.items():
        if name != book.part and readback.parts[name] != original:
            raise UpdateError(f'非目标部件发生变化：{name}')
    return data


def apply_update(plan: dict, *, expected_target_sha256: str | None = None) -> dict:
    """Revalidate the source and exact plan, then optimistically replace atomically.

    Advisory locking coordinates tool writers; an Excel lock file is rejected.
    A final inode/stat/hash comparison detects observable concurrent edits.
    Non-cooperating writers racing the final filesystem replace cannot be locked
    out by portable filesystem APIs; this does not claim a transactional Excel API.
    """
    expected = expected_target_sha256 or plan.get('target_sha256')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise UpdateError('实际写入必须带精确目标 SHA-256')
    fresh = plan_update(Path(plan['input']), plan['spec'])
    if fresh != plan or fresh['target_sha256'] != expected:
        raise UpdateError('来源、范围或目标已改变；旧预览不能写入，请重新预览')
    path = Path(plan['spec']['workbook'])
    temporary = None
    with path.open('rb') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError('另一更新进程正在使用目标，未写入') from exc
        original, identity = _target(path)
        if sha(original) != expected or os.fstat(lock.fileno()).st_ino != identity[1]:
            raise UpdateError('目标在获取写入锁前发生变化，未写入')
        if not plan['changes']:
            return {'status': 'unchanged', 'workbook': str(path), 'target_sha256': expected, 'changed_cells': 0}
        book = Book(original, plan['spec']['sheet'])
        data = _build_bytes(book, plan['changes'])
        try:
            with tempfile.NamedTemporaryFile(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                os.fchmod(output.fileno(), stat.S_IMODE(path.stat().st_mode))
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            # Reopen the actual temporary ZIP and read target values before commit.
            persisted = Book(temporary.read_bytes(), plan['spec']['sheet'])
            for change in plan['changes']:
                if persisted.value(change['cell']) != change['new']:
                    raise UpdateError('落盘后的临时工作簿回读失败')
            if plan_update(Path(plan['input']), plan['spec']) != plan:
                raise UpdateError('来源或目标在准备写入期间发生变化，未写入')
            latest, final_identity = _target(path)
            if final_identity != identity or sha(latest) != expected:
                raise UpdateError('目标在写入前发生并发变化，未写入')
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    final_data = path.read_bytes()
    result = {'status': 'saved', 'workbook': str(path), 'sheet': plan['spec']['sheet'], 'changed_cells': len(plan['changes']),
              'before_sha256': expected, 'after_sha256': sha(data), 'readback': 'passed', 'recalculation': 'not_performed'}
    if final_data != data:
        # A non-cooperating writer changed the file after our commit. Do not undo it.
        result.update(status='saved_then_changed', readback='target_changed_after_atomic_replace')
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--spec', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='在调用者已获得的授权范围实际写入；默认仅预览')
    parser.add_argument('--expected-target-sha256')
    parser.add_argument('--expected-plan-sha256', help='绑定先前预览的输入、范围、目标和逐格变更')
    args = parser.parse_args(argv)
    try:
        if args.apply and not args.expected_target_sha256:
            raise UpdateError('--apply 必须给出预览的 --expected-target-sha256')
        spec_path = args.spec.resolve()
        spec = json.loads(spec_path.read_text(encoding='utf-8'))
        if isinstance(spec, dict) and isinstance(spec.get('workbook'), str):
            workbook = Path(spec['workbook'])
            if spec['workbook'] and not workbook.is_absolute():
                # A relative target belongs to its spec, never the shell cwd.
                # Do not resolve the final component: _target rejects symlinks.
                spec['workbook'] = str(spec_path.parent / workbook)
        plan = plan_update(args.input, spec)
        if args.expected_plan_sha256 and plan['plan_sha256'] != args.expected_plan_sha256:
            raise UpdateError('预览摘要不匹配；输入、范围或目标已变，未写入')
        if args.expected_target_sha256 and plan['target_sha256'] != args.expected_target_sha256:
            raise UpdateError('目标摘要不匹配，未写入')
        result = apply_update(plan, expected_target_sha256=args.expected_target_sha256) if args.apply else plan
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('status') == 'saved_then_changed' else 0
    except (UpdateError, OSError, UnicodeError, ValueError, KeyError, TypeError, expat.ExpatError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
