"""V2 mapping must preserve source identity and exact reconciliation."""
import csv
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from packages.metricguard_core import compare, parse_definition
from packages.metricguard_core.common import CoreError, SCHEMA
from packages.metricguard_core.v2_contract import ingest_mapped, preview_source, validate_contract, validate_mapping
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import ingest_job
from services.supervisor.worker import Supervisor


CONTRACT = {
    'version': 2, 'name': 'Retail sales', 'currency': 'INR',
    'timezone': 'Asia/Kolkata', 'scope': 'retail_pilot',
    'grain': 'one_row_per_sale', 'cutoff_at': '2026-09-02',
    'null_policy': 'blank_nullable', 'row_key': 'order_id',
    'entity_key': 'customer_id', 'decimal_scale': 2, 'schema': SCHEMA,
}
MAPPING = {'columns': dict(zip(SCHEMA, (
    'sale_ref', 'buyer_ref', 'sold_on', 'paid_on', 'sale_state',
    'pay_state', 'gross_inr', 'returned_inr', 'iso_code')))}
HEADERS = list(MAPPING['columns'].values()) + ['unused_source_column']
ROWS = [
    ['s-1', 'b-1', '2026-08-02', '2026-08-03', 'completed', 'paid', '120.00', '0.00', 'INR', 'ignore'],
    ['s-2', 'b-2', '2026-08-04', '', 'refunded', 'paid', '50.00', '10.00', 'INR', 'ignore'],
]


def source_file(path: Path, rows=ROWS):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(rows)


def parquet_source(path: Path, *, rows=None, amount_type='DECIMAL(18,2)'):
    rows = rows if rows is not None else [
        ('s-1', 'b-1', '2026-08-02', '2026-08-03', 'completed',
         'paid', '120.00', '0.00', 'INR', 'ignore'),
        ('s-2', None, '2026-08-04', None, 'refunded',
         'paid', '50.00', None, 'INR', 'ignore'),
    ]
    with duckdb.connect(':memory:') as con:
        con.execute(f'''CREATE TABLE sales (
            sale_ref VARCHAR, buyer_ref VARCHAR, sold_on DATE, paid_on DATE,
            sale_state VARCHAR, pay_state VARCHAR, gross_inr {amount_type},
            returned_inr DECIMAL(18,2), iso_code VARCHAR,
            unused_source_column VARCHAR)''')
        con.executemany('INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
        con.execute('COPY sales TO ? (FORMAT PARQUET)', [str(path)])


def test_mapped_csv_keeps_original_hash_and_reconciles(tmp_path):
    source = tmp_path / 'source.csv'
    source_file(source)
    contract = validate_contract(CONTRACT)
    mapping = validate_mapping(MAPPING, contract)
    manifest = ingest_mapped(source, tmp_path / 'snapshot.duckdb', contract, mapping)
    assert manifest['row_count'] == 2
    assert manifest['source_hash'] != manifest['database_hash']
    assert manifest['contract_hash'] == contract['contract_hash']
    assert manifest['mapping_hash'] == mapping['mapping_hash']
    baseline = parse_definition('SELECT SUM(order_amount) FROM orders WHERE status = \'completed\'', 'order_date')
    target = parse_definition('SELECT SUM(order_amount) FROM orders WHERE payment_status = \'paid\'', 'order_date')
    context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01',
               'currency': 'INR', 'timezone': 'Asia/Kolkata',
               'scope': 'retail_pilot', 'grain': 'one_row_per_sale', 'unit': 'currency',
               'contract_hash': contract['contract_hash'], 'mapping_hash': mapping['mapping_hash']}
    result = compare(tmp_path / 'snapshot.duckdb', baseline, target, context)
    assert result['baseline_value'] == '120.00'
    assert result['target_value'] == '170.00'
    assert result['delta_target_minus_baseline'] == '50.00'
    assert result['residual'] == '0.00'


def test_mapped_parquet_preserves_native_nulls_and_source_identity(tmp_path):
    source = tmp_path / 'sales.parquet'
    parquet_source(source)
    contract = validate_contract({**CONTRACT, 'null_policy': 'native_null'})
    mapping = validate_mapping(MAPPING, contract)
    destination = tmp_path / 'snapshot.duckdb'

    manifest = ingest_mapped(source, destination, contract, mapping)

    assert manifest['ingestion_version'] == 'mapped-parquet-v2'
    assert manifest['row_count'] == 2
    assert manifest['source_hash'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest['contract_hash'] == contract['contract_hash']
    assert manifest['mapping_hash'] == mapping['mapping_hash']
    with duckdb.connect(str(destination)) as con:
        assert con.execute('SELECT order_id, customer_id, payment_date, refund_amount FROM orders ORDER BY order_id').fetchall() == [
            ('s-1', 'b-1', date(2026, 8, 3), Decimal('0.00')),
            ('s-2', None, None, None),
        ]
        assert [column[0] for column in con.execute('DESCRIBE orders').fetchall()] == list(SCHEMA)


@pytest.mark.parametrize('rows,amount_type,code', [
    ([('s-1', 'b-1', '2026-08-02', '2026-08-03', 'completed', 'paid', '120.00', '0.00', 'INR', 'x'),
      ('s-1', 'b-2', '2026-08-04', None, 'completed', 'paid', '50.00', None, 'INR', 'x')],
     'DECIMAL(18,2)', 'invalid_row'),
    ([('s-1', None, '2026-08-02', None, 'completed', 'paid', '120.00', None, 'INR', 'x')],
     'VARCHAR', 'invalid_schema'),
    ([(None, 'b-1', '2026-08-02', None, 'completed', 'paid', '120.00', None, 'INR', 'x')],
     'DECIMAL(18,2)', 'invalid_row'),
    ([('s-1', 'b-1', '2026-08-02', None, 'completed', 'paid', '120.00', None, 'USD', 'x')],
     'DECIMAL(18,2)', 'invalid_row'),
])
def test_mapped_parquet_rejects_invalid_rows_or_types(tmp_path, rows, amount_type, code):
    source = tmp_path / 'sales.parquet'
    parquet_source(source, rows=rows, amount_type=amount_type)
    destination = tmp_path / 'snapshot.duckdb'
    contract = validate_contract({**CONTRACT, 'null_policy': 'native_null'})
    with pytest.raises(CoreError) as error:
        ingest_mapped(source, destination, contract, validate_mapping(MAPPING, contract))
    assert error.value.code == code
    assert not destination.exists()


def test_mapped_parquet_requires_native_null_policy(tmp_path):
    source = tmp_path / 'sales.parquet'
    parquet_source(source)
    contract = validate_contract(CONTRACT)
    with pytest.raises(CoreError) as error:
        ingest_mapped(source, tmp_path / 'snapshot.duckdb', contract, validate_mapping(MAPPING, contract))
    assert error.value.code == 'invalid_contract'


def test_csv_preview_shows_five_rows_and_bounded_json(tmp_path):
    source = tmp_path / 'sales.csv'
    source_file(source, ROWS * 4)

    preview = preview_source(source)

    assert preview['headers'] == HEADERS
    assert preview['declared_types'] == dict.fromkeys(HEADERS)
    assert preview['observed_types']['sold_on'] == 'DATE'
    assert preview['observed_types']['gross_inr'] == 'DECIMAL(18,2)'
    assert len(preview['sample_rows']) == 5
    assert preview['sample_rows'][0]['sale_ref'] == 's-1'
    assert preview['source_hash'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(json.dumps(preview, ensure_ascii=True).encode()) <= 64 * 1024


def test_parquet_preview_serializes_typed_values_and_nulls(tmp_path):
    source = tmp_path / 'sales.parquet'
    parquet_source(source)

    preview = preview_source(source)

    assert preview['headers'] == HEADERS
    assert preview['declared_types']['sold_on'] == 'DATE'
    assert preview['declared_types']['gross_inr'] == 'DECIMAL(18,2)'
    assert preview['observed_types']['gross_inr'] == 'DECIMAL(18,2)'
    assert preview['sample_rows'][0]['sold_on'] == '2026-08-02'
    assert preview['sample_rows'][0]['gross_inr'] == '120.00'
    assert preview['sample_rows'][1]['paid_on'] is None
    assert preview['source_hash'] == hashlib.sha256(source.read_bytes()).hexdigest()
    json.dumps(preview)


def test_preview_truncates_large_cells_to_stay_below_64_kib(tmp_path):
    source = tmp_path / 'wide.csv'
    source_file(source, [[*ROWS[0][:-1], 'x' * 100_000]])

    preview = preview_source(source)

    assert preview['sample_rows'][0]['unused_source_column'].endswith('…')
    assert len(json.dumps(preview, ensure_ascii=True).encode()) <= 64 * 1024


def test_preview_bound_includes_json_key_spacing(tmp_path):
    source = tmp_path / 'many_columns.csv'
    with source.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow([f'column_{number}' for number in range(200)])
        writer.writerows([['x' * 37] * 200 for _ in range(5)])

    preview = preview_source(source)

    assert len(json.dumps(preview, ensure_ascii=False, allow_nan=False).encode()) <= 64 * 1024


def test_preview_rejects_more_than_one_million_parquet_rows(tmp_path):
    source = tmp_path / 'huge.parquet'
    with duckdb.connect(':memory:') as con:
        con.execute("COPY (SELECT i AS key FROM range(1000001) AS t(i)) TO ? (FORMAT PARQUET)", [str(source)])

    with pytest.raises(CoreError) as error:
        preview_source(source)

    assert error.value.code == 'too_many_rows'


@pytest.mark.parametrize('rows,code', [
    (ROWS + [ROWS[0]], 'invalid_row'),
    (ROWS[:1] + [[*ROWS[1][:-2], 'USD', 'ignore']], 'invalid_row'),
])
def test_mapped_csv_rejects_duplicate_key_or_mixed_currency(tmp_path, rows, code):
    source = tmp_path / 'source.csv'
    source_file(source, rows)
    with pytest.raises(CoreError) as error:
        ingest_mapped(source, tmp_path / 'snapshot.duckdb',
                      validate_contract(CONTRACT), validate_mapping(MAPPING, validate_contract(CONTRACT)))
    assert error.value.code == code
    assert not (tmp_path / 'snapshot.duckdb').exists()


def test_mapping_identity_changes_when_a_column_changes():
    contract = validate_contract(CONTRACT)
    first = validate_mapping(MAPPING, contract)
    changed = validate_mapping({'columns': {**MAPPING['columns'], 'status': 'different_state'}}, contract)
    assert first['mapping_hash'] != changed['mapping_hash']


def test_contract_rejects_unsupported_scale():
    with pytest.raises(CoreError) as error:
        validate_contract({**CONTRACT, 'decimal_scale': 3})
    assert error.value.code == 'invalid_contract'


def test_approved_mapping_upload_is_published_only_by_worker(tmp_path):
    class Completed:
        def poll(self): return 0
        def wait(self, timeout=None): return 0

    class InProcessLauncher:
        def start(self, input_dir, output_dir, command=None):
            assert command == 'ingest'
            ingest_job(input_dir, output_dir)
            return Completed()

    source = tmp_path / 'sales.csv'
    source_file(source)
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        approved = client.post('/api/v1/dataset-contracts', json=CONTRACT)
        assert approved.status_code == 201, approved.text
        mapping = client.post('/api/v1/column-mappings', json={
            'contract_id': approved.json()['id'], **MAPPING})
        assert mapping.status_code == 201, mapping.text
        queued = client.post('/api/v1/mapped-snapshots',
                             files={'file': ('sales.csv', source.read_bytes())},
                             data={'mapping_id': mapping.json()['id'], 'name': 'Sales extract'})
        assert queued.status_code == 202, queued.text
        assert queued.json()['state'] == 'queued'
        assert client.get('/api/v1/bootstrap').json()['snapshots'] == []
        worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs',
                            'mapped-worker', data_dir=tmp_path)
        assert worker.run_once()
        imported = client.get(f"/api/v1/snapshot-imports/{queued.json()['id']}").json()
        assert imported['state'] == 'validated', imported.get('error')
        snapshot = client.get(f"/api/v1/snapshots/{imported['snapshot_id']}").json()
        assert snapshot['row_count'] == 2
        assert snapshot['contract_hash'] == approved.json()['contract_hash']
        assert snapshot['mapping_hash'] == mapping.json()['mapping_hash']
