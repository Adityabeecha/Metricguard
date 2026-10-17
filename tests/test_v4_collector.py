"""Dedicated verification test suite for Milestone V4-02:
Read-Only PostgreSQL Source Collector & Cutoff Consistency.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import uuid
import pytest
import psycopg
from fastapi.testclient import TestClient

from packages.metricguard_core.common import SCHEMA, digest, file_hash
from packages.metricguard_core.snapshot import inspect_snapshot, manifest_path
from packages.metricguard_core import compare
from packages.metricguard_core.flat import compare_flat
from services.api.app import create_app
from services.api.store import DomainError, Store
from services.supervisor.collector import (
    CollectorError,
    PostgresCollector,
    PostgresSourceSelection,
    redact_credentials,
    sanitize_source_uri,
    validate_identifier,
    validate_cutoff_value,
)

PG_TEST_URL = os.environ.get(
    'METRICGUARD_PG_TEST_URL',
    'postgresql://metricguard:local-dev-only@127.0.0.1:5433/metricguard'
)


def is_pg_available() -> bool:
    try:
        with psycopg.connect(PG_TEST_URL, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not is_pg_available(),
    reason=f"PostgreSQL test database is unavailable at {sanitize_source_uri(PG_TEST_URL)}"
)


@pytest.fixture
def tmp_workspace(tmp_path: Path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / 'test.db'
    store = Store(f"sqlite:///{db_path}")
    collector = PostgresCollector(store=store, data_dir=data_dir)
    return {
        'data_dir': data_dir,
        'store': store,
        'collector': collector,
        'tmp_path': tmp_path,
    }


@pytest.fixture
def pg_schema():
    schema_name = f"test_mg_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema_name}"')
    yield schema_name
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


@pytest.fixture
def retail_contract(tmp_workspace):
    store: Store = tmp_workspace['store']
    payload = {
        'version': 2,
        'name': 'Retail Orders Contract',
        'currency': 'INR',
        'timezone': 'Asia/Kolkata',
        'scope': 'synthetic_retail_all_orders',
        'grain': 'one_row_per_order_id',
        'cutoff_at': '2026-09-02',
        'null_policy': 'blank_nullable',
        'row_key': 'order_id',
        'entity_key': 'customer_id',
        'decimal_scale': 2,
        'schema': SCHEMA,
    }
    return store.record_dataset_contract(payload)


@pytest.fixture
def flat_contract(tmp_workspace):
    store: Store = tmp_workspace['store']
    payload = {
        'version': 3,
        'mode': 'flat_snapshot',
        'name': 'Whole Snapshot Flat Contract',
        'row_key': 'order_id',
        'measure': 'order_amount',
        'precision': 18,
        'scale': 2,
        'categories': ['status', 'channel'],
        'measure_meaning': 'Net order total',
        'unit_label': 'INR',
        'values_comparable': True,
        'scope': 'entire_supplied_snapshot',
        'null_policy': 'blank_nullable',
    }
    return store.record_flat_contract(payload)


# ---------------------------------------------------------------------------
# 1. Successful Read-Only Retail Extraction & Manifest Integrity
# ---------------------------------------------------------------------------

def test_successful_read_only_retail_extraction(tmp_workspace, pg_schema, retail_contract):
    store = tmp_workspace['store']
    collector = tmp_workspace['collector']

    # Populate PostgreSQL source table
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders VALUES
                ('ord-1', 'cust-1', '2026-08-10', '2026-08-11', 'completed', 'paid', 100.50, NULL, 'INR'),
                ('ord-2', 'cust-2', '2026-08-20', '2026-08-21', 'completed', 'paid', 250.00, 10.00, 'INR'),
                ('ord-3', 'cust-3', '2026-09-02', '2026-09-02', 'pending', 'unpaid', 50.00, NULL, 'INR'),
                ('ord-4', 'cust-4', '2026-09-05', '2026-09-05', 'completed', 'paid', 999.00, NULL, 'INR')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
        snapshot_name='August 2026 Cutoff',
    )

    result = collector.collect(selection, project_id=store.project_id)
    assert result['row_count'] == 3  # ord-4 is after cutoff
    assert result['artifact_path']

    artifact = Path(result['artifact_path'])
    assert artifact.is_file()
    assert file_hash(artifact) == result['database_hash']

    # Verify manifest integrity
    manifest = inspect_snapshot(artifact)
    assert manifest['row_count'] == 3
    assert manifest['consistency_policy'] == 'REPEATABLE READ READ ONLY'
    assert manifest['manifest_version'] == 'postgresql-v4'
    assert manifest['source_type'] == 'postgresql'
    assert manifest['cutoff_at'] == '2026-09-02'
    assert 'local-dev-only' not in json.dumps(manifest)

    # Verify store registration
    snap_rec = store.snapshot(result['snapshot_id'])
    assert snap_rec['manifest_hash'] == result['manifest_hash']
    assert snap_rec['row_count'] == 3


# ---------------------------------------------------------------------------
# 2. Successful Read-Only Flat Extraction
# ---------------------------------------------------------------------------

def test_successful_read_only_flat_extraction(tmp_workspace, pg_schema, flat_contract):
    store = tmp_workspace['store']
    collector = tmp_workspace['collector']

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".flat_data (
                    order_id VARCHAR(64) PRIMARY KEY,
                    order_amount NUMERIC(18,2) NOT NULL,
                    status VARCHAR(32),
                    channel VARCHAR(32),
                    order_date DATE NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".flat_data VALUES
                ('f-1', 120.00, 'delivered', 'web', '2026-08-15'),
                ('f-2', 85.50, 'shipped', 'app', '2026-08-28')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='flat_data',
        cutoff_column='order_date',
        cutoff_value='2026-09-01',
        contract_id=flat_contract['id'],
    )

    result = collector.collect(selection, project_id=store.project_id)
    assert result['row_count'] == 2

    artifact = Path(result['artifact_path'])
    sidecar = Path(manifest_path(artifact))
    manifest = json.loads(sidecar.read_text(encoding='utf-8'))
    assert manifest['mode'] == 'flat_snapshot'
    assert manifest['policy'] == 'flat-snapshot-v1'
    assert manifest['row_count'] == 2


# ---------------------------------------------------------------------------
# 3. Read-Only Transaction Isolation Blocks Mutations
# ---------------------------------------------------------------------------

def test_read_only_transaction_blocks_mutations():
    with psycopg.connect(PG_TEST_URL, autocommit=False) as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute('CREATE TABLE sample_mutation (id int)')
        conn.rollback()


# ---------------------------------------------------------------------------
# 4. Contract Validation: Enforce Types and Required Columns
# ---------------------------------------------------------------------------

def test_contract_validation_enforces_decimal_scale(tmp_workspace, pg_schema, retail_contract):
    collector = tmp_workspace['collector']

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_scale (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,4) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            # 3 decimal places violates DECIMAL(18,2) contract
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_scale VALUES
                ('ord-bad-scale', 'c1', '2026-08-10', NULL, 'completed', 'paid', 100.555, NULL, 'INR')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_scale',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'INVALID_ROW'
    assert 'exceeds 2 decimal places' in exc_info.value.message


def test_contract_validation_enforces_currency(tmp_workspace, pg_schema, retail_contract):
    collector = tmp_workspace['collector']

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_curr (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_curr VALUES
                ('ord-usd', 'c1', '2026-08-10', NULL, 'completed', 'paid', 100.00, NULL, 'USD')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_curr',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'INVALID_ROW'
    assert 'Incompatible currency' in exc_info.value.message


# ---------------------------------------------------------------------------
# 5. Duplicate Key Rejection
# ---------------------------------------------------------------------------

def test_duplicate_key_rejection(tmp_workspace, pg_schema, retail_contract):
    collector = tmp_workspace['collector']
    staging_dir = tmp_workspace['data_dir'] / 'staging'

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_dup (
                    order_id VARCHAR(64),
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            # Table lacks primary key and contains duplicate IDs
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_dup VALUES
                ('dup-1', 'c1', '2026-08-10', NULL, 'completed', 'paid', 100.00, NULL, 'INR'),
                ('dup-1', 'c2', '2026-08-12', NULL, 'completed', 'paid', 200.00, NULL, 'INR')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_dup',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'DUPLICATE_KEY'

    # Staging files must be completely cleaned up
    assert len(list(staging_dir.glob('*.duckdb'))) == 0


# ---------------------------------------------------------------------------
# 6. Row Limit Enforcement
# ---------------------------------------------------------------------------

def test_row_limit_enforcement(tmp_workspace, pg_schema, retail_contract):
    collector = tmp_workspace['collector']

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_limit (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            for i in range(10):
                cur.execute(f'''
                    INSERT INTO "{pg_schema}".orders_limit VALUES
                    ('ord-{i}', 'c', '2026-08-10', NULL, 'completed', 'paid', 10.00, NULL, 'INR')
                ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_limit',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
        row_limit=5,  # Exceeded by 10 rows
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'TOO_MANY_ROWS'


# ---------------------------------------------------------------------------
# 7. Cutoff Enforcement & Parameterization
# ---------------------------------------------------------------------------

def test_cutoff_enforcement_and_parameterization(tmp_workspace, pg_schema, retail_contract):
    collector = tmp_workspace['collector']

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_cutoff (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_cutoff VALUES
                ('in-1', 'c', '2026-08-01', NULL, 'ok', 'paid', 10.00, NULL, 'INR'),
                ('in-2', 'c', '2026-08-31', NULL, 'ok', 'paid', 20.00, NULL, 'INR'),
                ('out-1', 'c', '2026-09-01', NULL, 'ok', 'paid', 30.00, NULL, 'INR'),
                ('out-2', 'c', '2026-09-02', NULL, 'ok', 'paid', 40.00, NULL, 'INR')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_cutoff',
        cutoff_column='order_date',
        cutoff_value='2026-08-31',
        contract_id=retail_contract['id'],
    )

    result = collector.collect(selection)
    assert result['row_count'] == 2


# ---------------------------------------------------------------------------
# 8. Malicious Identifier Injection Prevention
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('bad_table', [
    'orders; DROP TABLE orders; --',
    'orders" OR 1=1 --',
    'orders(col)',
    'orders--comment',
    'orders space',
    'orders/*',
])
def test_malicious_table_identifier_rejected(tmp_workspace, bad_table, retail_contract):
    collector = tmp_workspace['collector']
    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name='public',
        table_name=bad_table,
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )
    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'INVALID_IDENTIFIER'


@pytest.mark.parametrize('bad_schema', [
    'public; DROP SCHEMA public CASCADE;',
    'public" OR 1=1',
    'public.secret',
    'public--',
])
def test_malicious_schema_identifier_rejected(tmp_workspace, bad_schema, retail_contract):
    collector = tmp_workspace['collector']
    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=bad_schema,
        table_name='orders',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )
    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'INVALID_IDENTIFIER'


@pytest.mark.parametrize('bad_column', [
    'order_id; SELECT * FROM users',
    'order_id, (SELECT password FROM users)',
    'order_id" FROM orders; --',
    'order_id OR 1=1',
])
def test_malicious_column_identifier_rejected(tmp_workspace, bad_column, retail_contract):
    collector = tmp_workspace['collector']
    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name='public',
        table_name='orders',
        cutoff_column=bad_column,
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )
    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'INVALID_IDENTIFIER'


# ---------------------------------------------------------------------------
# 9. Malicious Cutoff Value Rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('bad_cutoff', [
    "'2026-09-02' OR 1=1",
    "2026-09-02; DROP TABLE orders;",
    "2026-09-02' UNION SELECT password FROM users --",
    "",
    "   ",
    "not-a-date",
])
def test_malicious_cutoff_value_rejected(tmp_workspace, bad_cutoff, retail_contract):
    collector = tmp_workspace['collector']
    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name='public',
        table_name='orders',
        cutoff_column='order_date',
        cutoff_value=bad_cutoff,
        contract_id=retail_contract['id'],
    )
    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'INVALID_CUTOFF'


# ---------------------------------------------------------------------------
# 10. Retry Behavior: Fixed Cutoff & Fresh Staging
# ---------------------------------------------------------------------------

def test_retry_behavior_fixed_cutoff_and_fresh_staging(tmp_workspace, pg_schema, retail_contract):
    store = tmp_workspace['store']
    collector = tmp_workspace['collector']
    extraction_id = f"ext-retry-{uuid.uuid4().hex}"

    # Setup source table
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_retry (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            # Initial bad row to cause failure
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_retry VALUES
                ('ord-1', 'c1', '2026-08-10', NULL, 'ok', 'paid', 100.00, NULL, 'EUR')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_retry',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
        extraction_id=extraction_id,
        attempt=1,
    )

    # Attempt 1 fails
    with pytest.raises(CollectorError):
        collector.collect(selection)

    ext_rec = store.get_extraction(extraction_id)
    assert ext_rec['state'] == 'failed'
    assert ext_rec['attempt'] == 1
    assert len(ext_rec['data']['attempts']) == 1

    # Attempt retry with DIFFERENT cutoff -> REJECTED
    selection_bad_cutoff = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_retry',
        cutoff_column='order_date',
        cutoff_value='2026-09-05',  # Changed!
        contract_id=retail_contract['id'],
        extraction_id=extraction_id,
        attempt=2,
    )
    with pytest.raises(DomainError) as exc_info:
        collector.collect(selection_bad_cutoff)
    assert exc_info.value.code == 'CUTOFF_CHANGED'

    # Fix the data in PostgreSQL source
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                UPDATE "{pg_schema}".orders_retry SET currency = 'INR' WHERE order_id = 'ord-1'
            ''')

    # Attempt 2 with IDENTICAL cutoff -> SUCCEEDS
    selection_retry = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_retry',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',  # Identical
        contract_id=retail_contract['id'],
        extraction_id=extraction_id,
        attempt=2,
    )
    result2 = collector.collect(selection_retry)
    assert result2['row_count'] == 1
    assert result2['attempt'] == 2

    ext_rec2 = store.get_extraction(extraction_id)
    assert ext_rec2['state'] == 'succeeded'
    assert ext_rec2['attempt'] == 2
    assert len(ext_rec2['data']['attempts']) == 2


# ---------------------------------------------------------------------------
# 11. Staging Cleanup on Failures
# ---------------------------------------------------------------------------

def test_failure_cleanup_on_connection_error(tmp_workspace, retail_contract):
    collector = tmp_workspace['collector']
    staging_dir = tmp_workspace['data_dir'] / 'staging'
    snapshots_dir = tmp_workspace['data_dir'] / 'snapshots'

    # Port 9999 has no postgres
    bad_uri = 'postgresql://metricguard:secret@127.0.0.1:9999/metricguard'
    selection = PostgresSourceSelection(
        source_uri=bad_uri,
        schema_name='public',
        table_name='orders',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'CONNECTION_FAILED'
    assert 'secret' not in exc_info.value.message

    assert len(list(staging_dir.glob('*'))) == 0
    assert len(list(snapshots_dir.glob('*'))) == 0


def test_failure_cleanup_on_mid_stream_network_error(tmp_workspace, pg_schema, retail_contract, monkeypatch):
    collector = tmp_workspace['collector']
    staging_dir = tmp_workspace['data_dir'] / 'staging'

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_net (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_net VALUES
                ('o-1', 'c', '2026-08-10', NULL, 'ok', 'paid', 10.00, NULL, 'INR')
            ''')

    def failing_fetchmany(self, size=None):
        raise psycopg.OperationalError("connection closed abruptly during fetch")

    monkeypatch.setattr(psycopg.ServerCursor, 'fetchmany', failing_fetchmany)

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_net',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'EXTRACTION_FAILED'

    # Staging files must be completely removed
    assert len(list(staging_dir.glob('*'))) == 0


def test_failure_after_transaction_begins_nonexistent_table(tmp_workspace, retail_contract):
    collector = tmp_workspace['collector']
    staging_dir = tmp_workspace['data_dir'] / 'staging'

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name='public',
        table_name='non_existent_table_xyz',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'EXTRACTION_FAILED'
    assert len(list(staging_dir.glob('*'))) == 0


def test_failure_while_writing_duckdb(tmp_workspace, pg_schema, retail_contract, monkeypatch):
    collector = tmp_workspace['collector']
    staging_dir = tmp_workspace['data_dir'] / 'staging'

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_ddb (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_ddb VALUES
                ('o-1', 'c', '2026-08-10', NULL, 'ok', 'paid', 10.00, NULL, 'INR')
            ''')

    import duckdb

    def failing_executemany(self, *args, **kwargs):
        raise duckdb.Error("Simulated disk error while writing DuckDB")

    monkeypatch.setattr(duckdb.DuckDBPyConnection, 'executemany', failing_executemany)

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_ddb',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert exc_info.value.code == 'EXTRACTION_FAILED'
    assert len(list(staging_dir.glob('*'))) == 0


def test_failure_after_manifest_before_store_registration(tmp_workspace, pg_schema, retail_contract, monkeypatch):
    store = tmp_workspace['store']
    collector = tmp_workspace['collector']
    staging_dir = tmp_workspace['data_dir'] / 'staging'
    snapshots_dir = tmp_workspace['data_dir'] / 'snapshots'

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_store_fail (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_store_fail VALUES
                ('o-1', 'c', '2026-08-10', NULL, 'ok', 'paid', 10.00, NULL, 'INR')
            ''')

    def failing_record_snapshot(*args, **kwargs):
        raise RuntimeError("Store database locked during registration")

    monkeypatch.setattr(store, 'record_snapshot', failing_record_snapshot)

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_store_fail',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
        extraction_id='ext-store-fail',
    )

    with pytest.raises(CollectorError) as exc_info:
        collector.collect(selection)
    assert 'Store database locked' in exc_info.value.message

    # Crucial guarantee: no partial snapshot or valid manifest left on disk!
    assert len(list(staging_dir.glob('*'))) == 0
    assert len(list(snapshots_dir.glob('*'))) == 0

    ext_rec = store.get_extraction('ext-store-fail')
    assert ext_rec['state'] == 'failed'
    assert ext_rec['error']['code'] == 'STORE_REGISTRATION_FAILED'


# ---------------------------------------------------------------------------
# 12. Source Mutation Test Demonstrating Immutability
# ---------------------------------------------------------------------------

def test_source_mutation_demonstrates_frozen_snapshot_immutability(tmp_workspace, pg_schema, retail_contract):
    store = tmp_workspace['store']
    collector = tmp_workspace['collector']

    # 1. Populate initial PostgreSQL state
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".mutation_orders (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".mutation_orders VALUES
                ('ord-1', 'cust-1', '2026-08-15', NULL, 'completed', 'paid', 100.00, NULL, 'INR'),
                ('ord-2', 'cust-2', '2026-08-20', NULL, 'completed', 'paid', 200.00, NULL, 'INR'),
                ('ord-3', 'cust-3', '2026-09-10', NULL, 'completed', 'paid', 500.00, NULL, 'INR')
            ''')

    # 2. Collect Snapshot A with cutoff 2026-09-02
    sel_a = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='mutation_orders',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
        snapshot_name='Snapshot A',
    )
    snap_a = collector.collect(sel_a)
    assert snap_a['row_count'] == 2  # ord-1 and ord-2

    # Verify Snapshot A content in DuckDB
    import duckdb
    with duckdb.connect(snap_a['artifact_path'], read_only=True) as ddb:
        total_a = ddb.execute('SELECT SUM(order_amount) FROM orders').fetchone()[0]
        assert total_a == Decimal('300.00')

    # 3. Mutate PostgreSQL source after Snapshot A is frozen
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Retroactively add an order before cutoff
            cur.execute(f'''
                INSERT INTO "{pg_schema}".mutation_orders VALUES
                ('ord-retro', 'cust-4', '2026-08-25', NULL, 'completed', 'paid', 150.00, NULL, 'INR')
            ''')
            # Mutate existing order_amount
            cur.execute(f'''
                UPDATE "{pg_schema}".mutation_orders SET order_amount = 999.00 WHERE order_id = 'ord-1'
            ''')

    # 4. Replay against Snapshot A: must be 100% identical and unaffected by mutations!
    with duckdb.connect(snap_a['artifact_path'], read_only=True) as ddb:
        rows_a = ddb.execute('SELECT order_id, order_amount FROM orders ORDER BY order_id').fetchall()
        assert rows_a == [('ord-1', Decimal('100.00')), ('ord-2', Decimal('200.00'))]
        total_replay = ddb.execute('SELECT SUM(order_amount) FROM orders').fetchone()[0]
        assert total_replay == Decimal('300.00')

    # Database hash must remain reproducible
    assert file_hash(snap_a['artifact_path']) == snap_a['database_hash']

    # 5. Collect Snapshot B under a new extraction
    sel_b = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='mutation_orders',
        cutoff_column='order_date',
        cutoff_value='2026-09-15',
        contract_id=retail_contract['id'],
        snapshot_name='Snapshot B',
    )
    snap_b = collector.collect(sel_b)
    assert snap_b['row_count'] == 4  # ord-1 (mutated), ord-2, ord-retro, ord-3
    assert snap_b['database_hash'] != snap_a['database_hash']
    assert snap_b['manifest_hash'] != snap_a['manifest_hash']

    with duckdb.connect(snap_b['artifact_path'], read_only=True) as ddb:
        total_b = ddb.execute('SELECT SUM(order_amount) FROM orders').fetchone()[0]
        assert total_b == Decimal('1849.00')  # 999 + 200 + 150 + 500


# ---------------------------------------------------------------------------
# 13. Credential Protection (Adversarial Tests)
# ---------------------------------------------------------------------------

def test_credential_protection_adversarial():
    secret_passwords = [
        'SuperSecretPass123',
        'P%40ss%3Aw0rd!%23$',
        'p@ss:w/ord',
        'ComplexPass#99&key=val',
    ]

    for pwd in secret_passwords:
        uri = f"postgresql://app_user:{pwd}@127.0.0.1:5432/finance_db"
        sanitized = sanitize_source_uri(uri)
        assert pwd not in sanitized
        assert '***' in sanitized

        # Exception masking
        err = CollectorError('TEST_CODE', f"Failed connection to {uri}")
        assert pwd not in err.message
        assert pwd not in repr(err)
        assert pwd not in str(err)

    # DSN format
    dsn = "host=db.internal user=admin password=MySecretDsnPass dbname=sales"
    sanitized_dsn = sanitize_source_uri(dsn)
    assert 'MySecretDsnPass' not in sanitized_dsn
    assert 'password=***' in sanitized_dsn


# ---------------------------------------------------------------------------
# 14. API RBAC and Cross-Project Isolation
# ---------------------------------------------------------------------------

def test_api_postgres_extract_rbac_and_cross_project(tmp_workspace, pg_schema, retail_contract):
    data_dir = tmp_workspace['data_dir']
    store: Store = tmp_workspace['store']

    # Setup source table in PostgreSQL
    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".api_orders (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".api_orders VALUES
                ('ord-api-1', 'c1', '2026-08-10', NULL, 'ok', 'paid', 100.00, NULL, 'INR')
            ''')

    # Create two projects and users with roles
    p1 = store.create_project(name='Project 1', project_id='proj-1')
    p2 = store.create_project(name='Project 2', project_id='proj-2')

    analyst_user, analyst_key = store.create_user(name='Analyst User', email='analyst@test.internal')
    reader_user, reader_key = store.create_user(name='Reader User', email='reader@test.internal')

    store.add_project_member('proj-1', analyst_user['id'], 'analyst')
    store.add_project_member('proj-1', reader_user['id'], 'reader')

    app = create_app(database_url=str(store.engine.url), data_dir=data_dir, mode='isolated')
    client = TestClient(app)

    payload = {
        'source_uri': PG_TEST_URL,
        'schema_name': pg_schema,
        'table_name': 'api_orders',
        'cutoff_column': 'order_date',
        'cutoff_value': '2026-09-02',
        'contract_id': retail_contract['id'],
    }

    # 1. Reader role is forbidden from triggering extraction
    res_reader = client.post(
        '/api/v1/projects/proj-1/sources/postgres/extract',
        json=payload,
        headers={'x-api-key': reader_key}
    )
    assert res_reader.status_code == 403
    assert res_reader.json()['error']['code'] == 'FORBIDDEN'

    # 2. Analyst role is permitted to trigger extraction
    res_analyst = client.post(
        '/api/v1/projects/proj-1/sources/postgres/extract',
        json=payload,
        headers={'x-api-key': analyst_key}
    )
    assert res_analyst.status_code == 200
    ext_data = res_analyst.json()
    assert ext_data['state'] == 'succeeded'
    assert ext_data['row_count'] == 1
    ext_id = ext_data['id']

    # 3. Reader CAN inspect extraction state in proj-1
    res_inspect = client.get(
        f'/api/v1/projects/proj-1/extractions/{ext_id}',
        headers={'x-api-key': reader_key}
    )
    assert res_inspect.status_code == 200
    assert res_inspect.json()['id'] == ext_id

    # 4. Cross-project IDOR check: accessing extraction from proj-2 is forbidden/not found
    res_idor = client.get(
        f'/api/v1/projects/proj-2/extractions/{ext_id}',
        headers={'x-api-key': analyst_key}
    )
    # Analyst has no role in proj-2 -> 403
    assert res_idor.status_code == 403

    # Add analyst to proj-2 and verify cross-project object mismatch returns 404
    store.add_project_member('proj-2', analyst_user['id'], 'analyst')
    res_idor_member = client.get(
        f'/api/v1/projects/proj-2/extractions/{ext_id}',
        headers={'x-api-key': analyst_key}
    )
    assert res_idor_member.status_code == 404


# ---------------------------------------------------------------------------
# 15. Analytical Replay Against Frozen Snapshot Only (Zero Network/Creds)
# ---------------------------------------------------------------------------

def test_runner_receives_frozen_snapshot_only_no_network_no_creds(tmp_workspace, pg_schema, retail_contract):
    store = tmp_workspace['store']
    collector = tmp_workspace['collector']

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                CREATE TABLE "{pg_schema}".orders_recon (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64),
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32),
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18,2) NOT NULL,
                    refund_amount NUMERIC(18,2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            cur.execute(f'''
                INSERT INTO "{pg_schema}".orders_recon VALUES
                ('o-1', 'c1', '2026-08-10', '2026-08-10', 'completed', 'paid', 100.00, NULL, 'INR'),
                ('o-2', 'c2', '2026-08-20', '2026-08-20', 'completed', 'paid', 200.00, NULL, 'INR')
            ''')

    selection = PostgresSourceSelection(
        source_uri=PG_TEST_URL,
        schema_name=pg_schema,
        table_name='orders_recon',
        cutoff_column='order_date',
        cutoff_value='2026-09-02',
        contract_id=retail_contract['id'],
    )
    result = collector.collect(selection, project_id=store.project_id)
    snapshot_path = Path(result['artifact_path'])

    # Setup definitions for comparison
    d1 = store.record_definition(
        name='Total Revenue Baseline',
        source_label='SQL',
        owner_label='Analyst',
        raw_sql="SELECT SUM(order_amount) AS val FROM orders WHERE status = 'completed'",
        time_column='order_date',
    )
    d2 = store.record_definition(
        name='Direct Revenue Target',
        source_label='SQL',
        owner_label='Analyst',
        raw_sql="SELECT SUM(order_amount) AS val FROM orders WHERE status = 'completed' AND payment_status = 'paid'",
        time_column='order_date',
    )

    baseline_v = d1['versions'][-1]
    target_v = d2['versions'][-1]

    context = {
        'start': '2026-08-01',
        'end_exclusive': '2026-09-01',
        'timezone': 'Asia/Kolkata',
        'currency': 'INR',
        'unit': 'currency',
        'grain': 'one_row_per_order_id',
        'scope': 'synthetic_retail_all_orders',
        'contract_hash': retail_contract['contract_hash'],
        'mapping_hash': None,
    }

    # Execute compare directly against the frozen DuckDB file
    recon = compare(
        database=snapshot_path,
        baseline=baseline_v['ir'],
        target=target_v['ir'],
        context=context
    )

    assert recon['baseline_value'] == '300.00'
    assert recon['target_value'] == '300.00'
    assert recon['delta_target_minus_baseline'] == '0.00'
    assert recon['provenance']['database_hash'] == result['database_hash']
