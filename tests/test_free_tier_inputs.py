"""Deployment caps narrow existing ingestion policies without changing frozen contracts."""
import csv
import io
import zipfile

import duckdb
import pytest

from packages.metricguard_core.common import SCHEMA
from packages.metricguard_core.free_tier import (
    FreeTierLimit, check_rows, check_upload_size, configured_limit,
)
from packages.metricguard_core.flat import ingest_flat, parse_flat, validate_contract
from packages.metricguard_core.flat_change import compare_snapshots
from packages.metricguard_core.parquet import ingest_parquet
from packages.metricguard_core.snapshot import ingest_csv
from packages.metricguard_core.v2_contract import ingest_mapped, preview_source
from packages.metricguard_core.xlsx import xlsx_to_csv


@pytest.fixture(autouse=True)
def clear_limits(monkeypatch):
    monkeypatch.delenv('METRICGUARD_MAX_ROWS', raising=False)
    monkeypatch.delenv('METRICGUARD_MAX_UPLOAD_MB', raising=False)


def _csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as output:
        csv.writer(output).writerows(rows)
    return path


def _flat(null_policy='blank_nullable'):
    return validate_contract({'version': 5, 'mode': 'flat_snapshot', 'name': 'Limited dataset',
        'row_key': 'id', 'measure': 'amount', 'precision': 12, 'scale': 2,
        'categories': ['status'], 'dimension_types': {'status': 'string'}, 'nullable_categories': [],
        'measure_meaning': 'Amount', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': null_policy,
        'predicate_policy': 'flat-boolean-v1'})


def _xlsx(path, source_rows=None):
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(path, 'w') as output:
        output.writestr('xl/workbook.xml', f'<workbook xmlns="{main}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Data" r:id="s"/></sheets></workbook>')
        output.writestr('xl/_rels/workbook.xml.rels',
            '<Relationships><Relationship Id="s" Target="worksheets/sheet1.xml"/></Relationships>')
        rows = source_rows if source_rows is not None else [['id', 'amount', 'status'], ['a', '1.00', 'paid'], ['b', '2.00', 'paid']]
        body = ''.join(f'<row r="{i}">' + ''.join(
            f'<c r="{letter}{i}" t="inlineStr"><is><t>{value}</t></is></c>'
            for letter, value in zip('ABC', row)) + '</row>' for i, row in enumerate(rows, 1))
        output.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{main}"><sheetData>{body}</sheetData></worksheet>')
    return path


@pytest.mark.parametrize('value', ['', '  '])
def test_unset_and_empty_keep_local_behavior(monkeypatch, value):
    assert configured_limit('METRICGUARD_MAX_ROWS') is None
    monkeypatch.setenv('METRICGUARD_MAX_ROWS', value)
    check_rows(5_000_000)
    check_upload_size(512 * 1024 * 1024)


@pytest.mark.parametrize('value', ['0', '-1', 'bad', '1.5'])
@pytest.mark.parametrize('env_name,check', [('METRICGUARD_MAX_ROWS', check_rows),
                                         ('METRICGUARD_MAX_UPLOAD_MB', check_upload_size)])
def test_invalid_config_fails_visibly(monkeypatch, value, env_name, check):
    monkeypatch.setenv(env_name, value)
    with pytest.raises(ValueError, match=f'{env_name} must be a positive integer'):
        check(0)


def test_exact_boundaries_and_public_error(monkeypatch):
    monkeypatch.setenv('METRICGUARD_MAX_ROWS', '50000')
    check_rows(50_000)
    with pytest.raises(FreeTierLimit) as caught:
        check_rows(120_000)
    assert caught.value.code == 'FREE_TIER_ROWS'
    assert caught.value.status == 413
    assert str(caught.value) == 'Free beta limit: 50,000 rows per dataset (this file has 120,000).'
    with pytest.raises(FreeTierLimit) as partial:
        check_rows(50_001, exact=False)
    assert str(partial.value) == 'Free beta limit: 50,000 rows per dataset (this file has more than 50,000).'
    assert '50,001' not in str(partial.value)
    monkeypatch.setenv('METRICGUARD_MAX_UPLOAD_MB', '1')
    check_upload_size(1024 * 1024)
    with pytest.raises(FreeTierLimit) as caught:
        check_upload_size(1024 * 1024 + 1)
    assert caught.value.code == 'FREE_TIER_UPLOAD'


@pytest.mark.parametrize('source_kind', ['csv', 'parquet', 'xlsx'])
@pytest.mark.parametrize('limited', [False, True])
def test_flat_sources_enforce_optional_cap(tmp_path, monkeypatch, source_kind, limited):
    source = tmp_path / f'source.{source_kind}'
    contract = _flat('native_null' if source_kind == 'parquet' else 'blank_nullable')
    if source_kind == 'csv':
        _csv(source, [['id', 'amount', 'status'], ['a', '1.00', 'paid'], ['b', '2.00', 'paid']])
    elif source_kind == 'xlsx':
        _xlsx(source)
    else:
        with duckdb.connect() as con:
            con.execute("COPY (SELECT id, CAST(amount AS DECIMAL(12,2)) amount, 'paid' status "
                        "FROM (VALUES ('a', '1.00'), ('b', '2.00')) t(id,amount)) TO ? (FORMAT PARQUET)", [str(source)])
    destination = tmp_path / 'snapshot.duckdb'
    if limited:
        monkeypatch.setenv('METRICGUARD_MAX_ROWS', '1')
        with pytest.raises(FreeTierLimit, match='1 rows per dataset') as caught:
            ingest_flat(source, destination, contract)
        observed = '2' if source_kind == 'parquet' else 'more than 1'
        assert f'this file has {observed}' in str(caught.value)
        assert not destination.exists()
    else:
        assert ingest_flat(source, destination, contract)['row_count'] == 2


@pytest.mark.parametrize('source_kind', ['csv', 'parquet'])
@pytest.mark.parametrize('mapped', [False, True])
@pytest.mark.parametrize('limited', [False, True])
def test_retail_and_mapped_sources(tmp_path, monkeypatch, source_kind, mapped, limited):
    source = tmp_path / f'source.{source_kind}'
    rows = [[key, 'c1', '2026-08-01', '2026-08-01', 'paid', 'paid', '1.00', '0.00', 'INR'] for key in ('a', 'b')]
    if source_kind == 'csv':
        _csv(source, [list(SCHEMA), *rows])
    else:
        with duckdb.connect() as con:
            con.execute('CREATE TABLE orders (' + ', '.join(f'"{n}" {t}' for n, t in SCHEMA.items()) + ')')
            con.executemany('INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
            con.execute('COPY orders TO ? (FORMAT PARQUET)', [str(source)])
    destination = tmp_path / 'snapshot.duckdb'
    contract = {'version': 2, 'name': 'Retail', 'currency': 'INR', 'timezone': 'Asia/Kolkata',
        'scope': 'retail', 'grain': 'one_row_per_sale', 'cutoff_at': '2026-09-02',
        'null_policy': 'native_null' if source_kind == 'parquet' else 'blank_nullable',
        'row_key': 'order_id', 'entity_key': 'customer_id', 'decimal_scale': 2, 'schema': SCHEMA}
    load = (lambda: ingest_mapped(source, destination, contract, {'columns': {n: n for n in SCHEMA}})) if mapped else (
        lambda: (ingest_csv if source_kind == 'csv' else ingest_parquet)(source, destination))
    if limited:
        monkeypatch.setenv('METRICGUARD_MAX_ROWS', '1')
        with pytest.raises(FreeTierLimit) as caught:
            load()
        observed = '2' if source_kind == 'parquet' else 'more than 1'
        assert f'this file has {observed}' in str(caught.value)
        with pytest.raises(FreeTierLimit) as preview:
            preview_source(source)
        assert f'this file has {observed}' in str(preview.value)
        assert not destination.exists()
    else:
        assert load()['row_count'] == 2
        assert len(preview_source(source)['sample_rows']) == 2


def _relational(tmp_path, version=2, oversized_dimension=False):
    from packages.metricguard_core.relational_snapshot import ingest_relational
    contract = {'version': version, 'mode': 'relational_snapshot', 'policy': 'relational-star-v1',
        'name': 'Star', 'fact': {'table': 'orders', 'key': 'id', 'grain': 'one row per order', 'categories': []},
        'dimensions': [{'role': 'customer', 'table': 'customers', 'key': 'customer_id',
            'grain': 'one row per customer', 'categories': ['segment'], 'fact_foreign_key': 'customer_id'}],
        'measure': {'column': 'amount', 'precision': 12, 'scale': 2}, 'measure_meaning': 'Amount',
        'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}
    if version == 1:
        dim = contract.pop('dimensions')[0]
        contract['dimension'] = {k: v for k, v in dim.items() if k not in ('role', 'fact_foreign_key')}
        contract['relationship'] = {'fact_foreign_key': 'customer_id', 'dimension_key': 'customer_id',
            'cardinality': 'many_to_one', 'missing_dimension_policy': 'preserve_and_report'}
        contract['policy'] = 'relational-snapshot-v1'
    if version == 3:
        from packages.metricguard_core.relational_star import PREPARED_POLICY
        contract['policy'] = PREPARED_POLICY
    source = tmp_path / 'bundle.zip'
    fact = [['id', 'customer_id', 'amount'], ['a', 'c1', '1.00']]
    if not oversized_dimension:
        fact.append(['b', 'c1', '2.00'])
    customers = [['customer_id', 'segment'], ['c1', 'paid'], ['c2', 'other']]
    with zipfile.ZipFile(source, 'w') as bundle:
        for name, rows in [('fact.csv', fact), ('dimension.csv' if version == 1 else 'dim_customer.csv', customers)]:
            output = io.StringIO(); csv.writer(output).writerows(rows)
            bundle.writestr(name, output.getvalue())
    destination = tmp_path / 'relational.duckdb'
    return lambda: ingest_relational(source, destination, contract), destination


@pytest.mark.parametrize('version', [1, 2, 3])
@pytest.mark.parametrize('limited', [False, True])
@pytest.mark.parametrize('dimension', [False, True])
def test_relational_fact_and_dimension_caps(tmp_path, monkeypatch, version, limited, dimension):
    load, destination = _relational(tmp_path, version, dimension)
    if limited:
        monkeypatch.setenv('METRICGUARD_MAX_ROWS', '1')
        with pytest.raises(FreeTierLimit):
            load()
        assert not destination.exists()
    else:
        assert load()['row_count'] == (1 if dimension else 2)


def test_prepared_snapshot_cap_and_cleanup(tmp_path, monkeypatch):
    from packages.metricguard_core.relational_prepared import prepare
    load, source = _relational(tmp_path)
    load()
    destination = tmp_path / 'prepared.duckdb'
    monkeypatch.setenv('METRICGUARD_MAX_ROWS', '1')
    with pytest.raises(FreeTierLimit):
        prepare(source, destination, selected_columns=['customer.segment'], joins={'customer': 'left'}, name='Prepared')
    assert not destination.exists()
    assert not destination.with_suffix('.lineage.jsonl').exists()


@pytest.mark.parametrize('version', [1, 2, 3])
def test_relational_zip_upload_cap_cannot_be_bypassed_with_trailing_bytes(tmp_path, monkeypatch, version):
    load, destination = _relational(tmp_path, version)
    with (tmp_path / 'bundle.zip').open('ab') as bundle:
        bundle.write(b'x' * (1024 * 1024))
    monkeypatch.setenv('METRICGUARD_MAX_UPLOAD_MB', '1')
    with pytest.raises(FreeTierLimit) as caught:
        load()
    assert caught.value.code == 'FREE_TIER_UPLOAD'
    assert not destination.exists()


def test_two_load_union_cannot_exceed_cap(tmp_path, monkeypatch):
    contract = _flat()
    snapshots = []
    for name, ids in [('before', ('a', 'b')), ('after', ('b', 'c'))]:
        source = _csv(tmp_path / f'{name}.csv', [['id', 'amount', 'status'], *[[i, '1.00', 'paid'] for i in ids]])
        destination = tmp_path / f'{name}.duckdb'
        ingest_flat(source, destination, contract)
        snapshots.append(destination)
    context = {'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
        'unit_label': 'INR', 'values_comparable': True, 'comparison': 'snapshot_change'}
    ir = parse_flat('SELECT SUM(amount) FROM snapshot', contract)
    assert compare_snapshots(*snapshots, ir, context)['residual'] == '0'
    monkeypatch.setenv('METRICGUARD_MAX_ROWS', '2')
    with pytest.raises(FreeTierLimit, match='this file has more than 2'):
        compare_snapshots(*snapshots, ir, context)


@pytest.mark.parametrize('source_kind', ['csv', 'parquet', 'xlsx'])
def test_upload_caps_before_parsing(tmp_path, monkeypatch, source_kind):
    source = tmp_path / f'large.{source_kind}'
    source.write_bytes(b'x' * (1024 * 1024 + 1))
    monkeypatch.setenv('METRICGUARD_MAX_UPLOAD_MB', '1')
    contract = _flat('native_null' if source_kind == 'parquet' else 'blank_nullable')
    with pytest.raises(FreeTierLimit) as caught:
        ingest_flat(source, tmp_path / 'out.duckdb', contract)
    assert caught.value.code == 'FREE_TIER_UPLOAD'
    with pytest.raises(FreeTierLimit):
        (xlsx_to_csv(source, tmp_path / 'converted.csv') if source_kind == 'xlsx' else preview_source(source))


@pytest.mark.parametrize('kind', ['snowflake', 'bigquery'])
@pytest.mark.parametrize('limited', [False, True])
def test_warehouse_extraction_stops_streaming(tmp_path, monkeypatch, kind, limited):
    from services.supervisor import warehouse_rest as warehouse
    from services.supervisor.collector import CollectorError
    args = {'kind': kind, 'token': 'test-secret-token', 'table': 'orders', 'columns': [('id', 'string'), ('amount', 'amount')],
            'row_key': 'id', 'amount_scales': {'amount': 2}}
    args.update({'account': 'myorg-account', 'database': 'analytics', 'schema': 'public', 'warehouse': 'compute'}
                if kind == 'snowflake' else {'project': 'project-123', 'dataset': 'analytics'})
    selection = warehouse.WarehouseSelection(**args)
    visited = []
    def rows(*_):
        for key in ('a', 'b', 'c'):
            visited.append(key)
            yield [key, '1.00']
    monkeypatch.setattr(warehouse, 'snowflake_rows' if kind == 'snowflake' else 'bigquery_rows', rows)
    if limited:
        monkeypatch.setenv('METRICGUARD_MAX_ROWS', '1')
        with pytest.raises(CollectorError) as caught:
            warehouse.extract_to_csv(selection, tmp_path / 'extract.csv', client=object())
        assert (caught.value.code, caught.value.status) == ('FREE_TIER_ROWS', 413)
        assert 'this file has more than 1' in caught.value.message
        assert visited == ['a', 'b']
    else:
        assert warehouse.extract_to_csv(selection, tmp_path / 'extract.csv', client=object()) == 3


@pytest.mark.parametrize('limited', [False, True])
def test_postgres_streaming_cap_before_bulk_load(tmp_path, monkeypatch, limited):
    from decimal import Decimal
    from services.supervisor.collector import PostgresCollector, PostgresSourceSelection
    class Cursor:
        def __init__(self):
            self.done = False
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def execute(self, *_):
            pass
        def fetchmany(self, *_):
            if self.done:
                return []
            self.done = True
            return [('a', Decimal('1.00'), 'paid'), ('b', Decimal('2.00'), 'paid')]
    class Connection:
        def cursor(self, **_):
            return Cursor()
    selection = PostgresSourceSelection(source_uri='postgresql://unused', table_name='orders',
                                       cutoff_column='created_at', cutoff_value='2026-09-01')
    destination = tmp_path / 'stage.duckdb'
    invoke = lambda: PostgresCollector(data_dir=tmp_path)._stream_and_stage(
        Connection(), selection, '2026-09-01', {'is_flat': True, 'contract': _flat()}, destination)
    if limited:
        monkeypatch.setenv('METRICGUARD_MAX_ROWS', '1')
        with pytest.raises(FreeTierLimit):
            invoke()
        assert not destination.with_name(destination.name + '.rows.csv').exists()
    else:
        assert invoke() == 2


@pytest.mark.parametrize('source_kind', ['csv', 'parquet', 'xlsx'])
@pytest.mark.parametrize('total', [0, 2, 8])
def test_preview_reports_full_row_count_beyond_display_sample(tmp_path, source_kind, total):
    from services.supervisor.runner import preview_job
    source = tmp_path / f'source.{source_kind}'
    rows = [[str(i), '1.00', 'paid'] for i in range(total)]
    if source_kind == 'csv':
        _csv(source, [['id', 'amount', 'status'], *rows])
    elif source_kind == 'xlsx':
        _xlsx(source, [['id', 'amount', 'status'], *rows])
    else:
        with duckdb.connect() as connection:
            connection.execute('CREATE TABLE dataset (id VARCHAR, amount DECIMAL(12,2), status VARCHAR)')
            if rows:
                connection.executemany('INSERT INTO dataset VALUES (?, ?, ?)', rows)
            connection.execute('COPY dataset TO ? (FORMAT PARQUET)', [str(source)])
    result = preview_job(tmp_path, tmp_path / 'output')
    assert result['row_count'] == total
    assert len(result['sample_rows']) == min(total, 5)
