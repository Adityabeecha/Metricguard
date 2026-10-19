"""Strict Excel (.xlsx) reader: the first worksheet becomes a CSV for the normal flat import.

Standard library only (zipfile + ElementTree), run inside the isolated runner because the
workbook is untrusted input. Values are rendered exactly, never rounded:

* numbers use the shortest text that round-trips the stored IEEE double, which is what Excel
  displays for typed values (19.99 stays "19.99"); a computed float such as
  0.30000000000000004 is kept as is, so the contract's decimal scale refuses it rather than
  MetricGuard silently rounding it;
* cells formatted as dates become ISO dates (2026-09-01) or timestamps;
* TRUE/FALSE become true/false; blank cells become blank (NULL under blank_nullable);
* a formula without a saved result, an error cell (#N/A, #DIV/0!) or a merged-cell layout
  is refused with its cell reference instead of being imported as blank.
"""
from __future__ import annotations

import csv
import math
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from .common import CoreError

MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024  # bounds zip bombs; the CSV limit still applies after conversion
MAX_COLUMNS = 512
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
# Built-in number formats that display dates or times (ECMA-376 18.8.30).
BUILTIN_DATE_FORMATS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 27, 30, 36, 45, 46, 47, 50, 57}
CELL_REF = re.compile(r'([A-Z]{1,3})([0-9]+)\Z')
EXCEL_EPOCH = datetime(1899, 12, 30)  # the 1900 date system, including Excel's 1900 leap-year quirk


def _fail(message):
    raise CoreError('invalid_xlsx', message)


def _column_index(letters):
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def _read(bundle, name):
    try:
        return ElementTree.fromstring(bundle.read(name))
    except KeyError:
        return None
    except ElementTree.ParseError as error:
        raise CoreError('invalid_xlsx', f'The workbook part {name} is not valid XML') from error


def _first_sheet_path(bundle):
    workbook = _read(bundle, 'xl/workbook.xml')
    if workbook is None:
        _fail('This is not an Excel workbook (.xlsx). Older .xls files must be saved as .xlsx or CSV first.')
    if workbook.find('m:workbookPr', NS) is not None and workbook.find('m:workbookPr', NS).get('date1904') in ('1', 'true'):
        _fail('Workbooks using the 1904 date system are not supported; save a copy with the 1900 date system.')
    sheet = workbook.find('m:sheets/m:sheet', NS)
    if sheet is None:
        _fail('The workbook has no worksheets.')
    target_id = sheet.get(f'{{{NS["r"]}}}id')
    rels = _read(bundle, 'xl/_rels/workbook.xml.rels')
    for rel in (rels if rels is not None else []):
        if rel.get('Id') == target_id:
            target = rel.get('Target', '')
            path = PurePosixPath(target.lstrip('/')) if target.startswith('/') else PurePosixPath('xl') / target
            parts = []
            for part in path.parts:  # normalise "..", never escaping the package
                if part == '..':
                    if parts:
                        parts.pop()
                elif part not in ('', '.'):
                    parts.append(part)
            return '/'.join(parts), sheet.get('name', 'Sheet1')
    _fail('The first worksheet could not be located in the workbook.')


def _shared_strings(bundle):
    root = _read(bundle, 'xl/sharedStrings.xml')
    if root is None:
        return []
    return [''.join(t.text or '' for t in item.iter(f'{{{NS["m"]}}}t')) for item in root.findall('m:si', NS)]


def _date_styles(bundle):
    """Indexes of cell styles (the s attribute) whose number format shows a date or time."""
    root = _read(bundle, 'xl/styles.xml')
    if root is None:
        return set()
    custom = {}
    for fmt in root.findall('m:numFmts/m:numFmt', NS):
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', fmt.get('formatCode', '')).lower()
        custom[int(fmt.get('numFmtId', '-1'))] = bool(re.search(r'[dmyhs]', code)) and 'general' not in code
    dates = set()
    for index, xf in enumerate(root.findall('m:cellXfs/m:xf', NS)):
        fmt = int(xf.get('numFmtId', '0'))
        if fmt in BUILTIN_DATE_FORMATS or custom.get(fmt):
            dates.add(index)
    return dates


def _number_text(raw, ref):
    try:
        value = float(raw)
    except ValueError as error:
        raise CoreError('invalid_xlsx', f'Cell {ref} holds a number Excel did not write correctly') from error
    if not math.isfinite(value):
        _fail(f'Cell {ref} is not a finite number.')
    if value.is_integer() and abs(value) < 2 ** 53:
        return str(int(value))
    return repr(value)  # shortest round-trip text, never rounded further


def _date_text(raw, ref):
    serial = float(raw)
    if serial < 1:
        _fail(f'Cell {ref} holds a time of day without a date; store it as text instead.')
    moment = EXCEL_EPOCH + timedelta(days=serial)
    moment = datetime(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second,
                      round(moment.microsecond / 1000) * 1000 % 1_000_000)
    return moment.date().isoformat() if serial.is_integer() else moment.isoformat(sep=' ')


def xlsx_to_csv(source, destination):
    """Write the first worksheet of `source` as UTF-8 CSV; returns (sheet name, data rows)."""
    source, destination = Path(source), Path(destination)
    try:
        bundle = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as error:
        raise CoreError('invalid_xlsx', 'This is not an Excel workbook (.xlsx). Older .xls files must be saved as .xlsx or CSV first.') from error
    with bundle:
        if sum(info.file_size for info in bundle.infolist()) > MAX_UNCOMPRESSED_BYTES:
            _fail('The workbook expands to more than 1 GiB.')
        sheet_path, sheet_name = _first_sheet_path(bundle)
        strings, date_styles = _shared_strings(bundle), _date_styles(bundle)
        try:
            stream = bundle.open(sheet_path)
        except KeyError as error:
            raise CoreError('invalid_xlsx', 'The first worksheet is missing from the workbook.') from error
        rows_written = 0
        width = None
        with stream, destination.open('w', encoding='utf-8', newline='') as output:
            writer = csv.writer(output, lineterminator='\n')
            m = f'{{{NS["m"]}}}'
            try:
                for _, element in ElementTree.iterparse(stream, events=('end',)):
                    if element.tag == m + 'mergeCell':
                        _fail(f'The sheet has merged cells ({element.get("ref")}); unmerge them so each row is one record.')
                    if element.tag != m + 'row':
                        continue
                    cells = {}
                    for cell in element.findall(m + 'c'):
                        ref = cell.get('r', '')
                        match = CELL_REF.match(ref)
                        if not match:
                            _fail('The sheet has a cell without a position; save it again in Excel.')
                        column = _column_index(match.group(1))
                        if column >= MAX_COLUMNS:
                            _fail(f'The sheet has more than {MAX_COLUMNS} columns.')
                        kind, value_node = cell.get('t', 'n'), cell.find(m + 'v')
                        raw = value_node.text if value_node is not None else None
                        if kind == 'inlineStr':
                            text = ''.join(t.text or '' for t in cell.iter(m + 't'))
                        elif raw is None:
                            if cell.find(m + 'f') is not None:
                                _fail(f'Cell {ref} has a formula without a saved result; open the file in Excel and save it again.')
                            text = ''
                        elif kind == 's':
                            index = int(raw)
                            if not 0 <= index < len(strings):
                                _fail(f'Cell {ref} refers to text the workbook does not contain.')
                            text = strings[index]
                        elif kind == 'b':
                            text = 'true' if raw == '1' else 'false'
                        elif kind == 'e':
                            _fail(f'Cell {ref} holds an Excel error ({raw}); fix it before importing.')
                        elif kind in ('str', 'd'):
                            text = raw
                        elif int(cell.get('s', '0')) in date_styles:
                            text = _date_text(raw, ref)
                        else:
                            text = _number_text(raw, ref)
                        cells[column] = text
                    row_ref = element.get('r')
                    element.clear()
                    if not cells or all(v == '' for v in cells.values()):
                        continue  # fully blank rows (Excel keeps formatted empty rows)
                    if width is None:
                        width = max(cells) + 1  # the header row fixes the columns
                    elif max(cells) >= width:
                        _fail(f'Row {row_ref} has values to the right of the header row.')
                    writer.writerow([cells.get(i, '') for i in range(width)])
                    rows_written += 1
            except ElementTree.ParseError as error:
                raise CoreError('invalid_xlsx', 'The worksheet is not valid XML') from error
    if rows_written == 0:
        _fail('The first worksheet is empty.')
    return sheet_name, rows_written - 1
