"""Excel (.xlsx) uploads: the first worksheet is read exactly and imported like a CSV."""
import csv
import zipfile

import pytest

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.flat import compare_flat, ingest_flat, parse_flat, validate_contract
from packages.metricguard_core.xlsx import xlsx_to_csv

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="xml" ContentType="application/xml"/></Types>')
WORKBOOK = ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Orders" sheetId="1" r:id="rId1"/><sheet name="Notes" sheetId="2" r:id="rId2"/></sheets></workbook>')
RELS = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId2" Type="worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
# Style 0 = General, style 1 = built-in date format 14, style 2 = custom "dd/mm/yyyy".
STYLES = ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
          '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs></styleSheet>')
STRINGS = ['order_id', 'amount', 'status', 'ordered_on', 'paid', 'O1', 'O2', 'O3', 'refunded', 'O4']


def _sheet(rows, extra=''):
    body = ''.join(f'<row r="{n}">{cells}</row>' for n, cells in rows)
    return ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{body}</sheetData>{extra}</worksheet>')


def _s(ref, text):
    return f'<c r="{ref}" t="s"><v>{STRINGS.index(text)}</v></c>'


GOOD_ROWS = [
    (1, _s('A1', 'order_id') + _s('B1', 'amount') + _s('C1', 'status') + _s('D1', 'ordered_on')),
    (2, _s('A2', 'O1') + '<c r="B2"><v>19.99</v></c>' + _s('C2', 'paid') + '<c r="D2" s="1"><v>46266</v></c>'),
    # O2: blank amount (no cell at all), a custom-format date
    (3, _s('A3', 'O2') + _s('C3', 'paid') + '<c r="D3" s="2"><v>46267</v></c>'),
    # O3: whole number stored as 250, inline text status, a formula with its saved result
    (4, _s('A4', 'O3') + '<c r="B4"><f>200+50</f><v>250</v></c><c r="C4" t="inlineStr"><is><t>refunded</t></is></c>'
        '<c r="D4" s="1"><v>46268</v></c>'),
    (6, ''),  # an empty formatted row Excel kept
    (7, _s('A7', 'O4') + '<c r="B7"><v>-0.5</v></c>' + _s('C7', 'paid') + '<c r="D7" t="str"><v>2026-09-04</v></c>'),
]


def _workbook(path, rows=GOOD_ROWS, extra=''):
    with zipfile.ZipFile(path, 'w') as bundle:
        bundle.writestr('[Content_Types].xml', CONTENT_TYPES)
        bundle.writestr('xl/workbook.xml', WORKBOOK)
        bundle.writestr('xl/_rels/workbook.xml.rels', RELS)
        bundle.writestr('xl/styles.xml', STYLES)
        bundle.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
                        ''.join(f'<si><t>{t}</t></si>' for t in STRINGS) + '</sst>')
        bundle.writestr('xl/worksheets/sheet1.xml', _sheet(rows, extra))
        bundle.writestr('xl/worksheets/sheet2.xml', _sheet([(1, '<c r="A1" t="inlineStr"><is><t>ignored</t></is></c>')]))
    return path


def test_first_sheet_becomes_exact_csv(tmp_path):
    sheet, rows = xlsx_to_csv(_workbook(tmp_path / 'orders.xlsx'), tmp_path / 'out.csv')
    assert (sheet, rows) == ('Orders', 4)
    with (tmp_path / 'out.csv').open(encoding='utf-8', newline='') as stream:
        assert list(csv.reader(stream)) == [
            ['order_id', 'amount', 'status', 'ordered_on'],
            ['O1', '19.99', 'paid', '2026-09-01'],
            ['O2', '', 'paid', '2026-09-02'],
            ['O3', '250', 'refunded', '2026-09-03'],
            ['O4', '-0.5', 'paid', '2026-09-04']]


def _contract():
    return validate_contract({'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'xlsx',
        'row_key': 'order_id', 'measure': 'amount', 'precision': 12, 'scale': 2, 'categories': ['status', 'ordered_on'],
        'dimension_types': {'status': 'string', 'ordered_on': 'date'}, 'nullable_categories': [],
        'measure_meaning': 'Order value', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})


def test_excel_upload_imports_and_compares_exactly(tmp_path):
    c = _contract()
    source = _workbook(tmp_path / 'orders.xlsx')
    manifest = ingest_flat(source, tmp_path / 'snap.duckdb', c)
    assert manifest['row_count'] == 4
    from packages.metricguard_core.common import file_hash
    assert manifest['source_hash'] == file_hash(source)  # the frozen identity is the uploaded workbook
    context = {'mode': 'flat_snapshot', 'scope': c['scope'], 'contract_hash': c['contract_hash'],
               'unit_label': 'INR', 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
    result = compare_flat(tmp_path / 'snap.duckdb', parse_flat('SELECT SUM(amount) FROM snapshot', c),
                          parse_flat("SELECT SUM(amount) FROM snapshot WHERE status = 'paid' AND ordered_on >= DATE '2026-09-02'", c), context)
    assert (result['baseline_value'], result['target_value']) == ('269.49', '-0.50')


@pytest.mark.parametrize('rows,extra,message', [
    ([(1, _s('A1', 'order_id')), (2, '<c r="A2"><f>1/0</f></c>')], '', 'formula without a saved result'),
    ([(1, _s('A1', 'order_id')), (2, '<c r="A2" t="e"><v>#DIV/0!</v></c>')], '', 'Excel error'),
    (GOOD_ROWS, '<mergeCells count="1"><mergeCell ref="A2:A3"/></mergeCells>', 'merged cells'),
    ([(1, _s('A1', 'order_id')), (2, _s('A2', 'O1') + '<c r="C2"><v>1</v></c>')], '', 'right of the header'),
    ([(1, '')], '', 'empty'),
])
def test_unsafe_sheets_are_refused_with_the_cell(tmp_path, rows, extra, message):
    with pytest.raises(CoreError, match=message):
        xlsx_to_csv(_workbook(tmp_path / 'bad.xlsx', rows, extra), tmp_path / 'out.csv')


def test_computed_float_is_refused_by_the_scale_not_rounded(tmp_path):
    rows = [GOOD_ROWS[0], (2, _s('A2', 'O1') + '<c r="B2"><v>0.30000000000000004</v></c>' + _s('C2', 'paid') + '<c r="D2" s="1"><v>46266</v></c>')]
    with pytest.raises(CoreError, match='exceeds declared DECIMAL'):
        ingest_flat(_workbook(tmp_path / 'float.xlsx', rows), tmp_path / 'snap.duckdb', _contract())


def test_old_xls_and_other_files_get_a_plain_message(tmp_path):
    fake = tmp_path / 'old.xlsx'
    fake.write_bytes(b'\xd0\xcf\x11\xe0 legacy binary workbook')
    with pytest.raises(CoreError, match='Older .xls files'):
        xlsx_to_csv(fake, tmp_path / 'out.csv')


def test_preview_job_reports_the_workbook_hash_and_a_csv_head(tmp_path):
    """The supervisor accepts a preview only if its source_hash is the uploaded file's; Excel previews
    also carry the converted CSV's first lines so the browser infers columns as for a CSV."""
    from packages.metricguard_core.common import file_hash
    from services.supervisor.runner import preview_job
    input_dir, output_dir = tmp_path / 'input', tmp_path / 'output'
    input_dir.mkdir()
    book = _workbook(input_dir / 'source.xlsx')
    preview = preview_job(input_dir, output_dir)
    assert preview['source_hash'] == file_hash(book)
    assert preview['sheet_name'] == 'Orders' and preview['headers'] == ['order_id', 'amount', 'status', 'ordered_on']
    assert preview['csv_head'].splitlines()[1] == 'O1,19.99,paid,2026-09-01'
