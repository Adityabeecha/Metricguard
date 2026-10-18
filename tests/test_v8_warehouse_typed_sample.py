"""Warehouse source: typed filter columns and sample-then-full extraction from PostgreSQL."""
import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

psycopg = pytest.importorskip('psycopg')

from packages.metricguard_core.flat import compare_flat, parse_flat, validate_contract
from services.supervisor.collector import CollectorError, PostgresCollector, PostgresSourceSelection

PG_URL = os.environ.get('METRICGUARD_TEST_PG_URL', 'postgresql://metricguard:local-dev-only@127.0.0.1:5433/metricguard')


def _reachable():
    try:
        psycopg.connect(PG_URL, connect_timeout=2).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason='PostgreSQL test database not reachable')


@pytest.fixture
def table():
    name = 'mg_wh_typed'
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute(f'DROP TABLE IF EXISTS {name}')
        conn.execute(f'CREATE TABLE {name} (order_id text primary key, amount numeric(18,2), status text, is_promo boolean, order_day date, created_at date)')
        rows = [(f'O{i:04d}', None if i % 50 == 0 else Decimal(i) / 4, ['completed', 'pending', 'returned'][i % 3],
                 None if i % 7 == 0 else i % 2 == 0, date(2026, 1, 1) + timedelta(days=i % 90), date(2026, 1, 1)) for i in range(1, 301)]
        with conn.cursor() as cur:
            cur.executemany(f'INSERT INTO {name} VALUES (%s,%s,%s,%s,%s,%s)', rows)
    yield name, rows
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute(f'DROP TABLE IF EXISTS {name}')


def _contract():
    return validate_contract({
        'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'warehouse typed',
        'row_key': 'order_id', 'measure': 'amount', 'precision': 18, 'scale': 2,
        'categories': ['status', 'is_promo', 'order_day'],
        'dimension_types': {'status': 'string', 'is_promo': 'boolean', 'order_day': 'date'},
        'nullable_categories': ['status', 'is_promo', 'order_day'], 'measure_meaning': 'Order amount', 'unit_label': 'INR',
        'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'native_null'})


def test_sample_then_full_with_typed_filters(table, tmp_path):
    name, rows = table
    contract = _contract()
    collector = PostgresCollector(data_dir=tmp_path)
    base = dict(source_uri=PG_URL, table_name=name, cutoff_column='created_at', cutoff_value='2026-12-31', contract=contract)
    sample = collector.collect(PostgresSourceSelection(**base, sample_rows=25, snapshot_name='wh'))
    assert sample['row_count'] == 25 and sample['manifest']['sample_rows'] == 25
    assert sample['manifest']['contract_version'] == 5 and 'sample of 25 rows' in sample['manifest']['name']
    full = collector.collect(PostgresSourceSelection(**base))
    assert full['row_count'] == 300 and 'sample_rows' not in full['manifest']
    assert full['manifest']['schema']['order_day'] == 'DATE' and full['manifest']['schema']['is_promo'] == 'BOOLEAN'

    context = {'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
               'unit_label': 'INR', 'values_comparable': True}
    db = full['artifact_path']
    result = compare_flat(db, parse_flat('SELECT SUM(amount) FROM snapshot', contract),
                          parse_flat("SELECT SUM(amount) FROM snapshot WHERE is_promo = TRUE AND order_day >= DATE '2026-02-01'", contract), context)
    expected_all = sum((r[1] for r in rows if r[1] is not None), Decimal(0))
    expected_target = sum((r[1] for r in rows if r[1] is not None and r[3] is True and r[4] >= date(2026, 2, 1)), Decimal(0))
    assert Decimal(result['baseline_value']) == expected_all and Decimal(result['target_value']) == expected_target
    count = compare_flat(db, parse_flat('SELECT COUNT(*) FROM snapshot', contract),
                         parse_flat('SELECT COUNT(*) FROM snapshot WHERE is_promo IS NULL', contract), context)
    assert (count['baseline_value'], count['target_value']) == ('300', str(sum(1 for r in rows if r[3] is None)))


def test_wrong_column_type_is_rejected(table, tmp_path):
    name, _ = table
    payload = {k: v for k, v in _contract().items() if k != 'contract_hash'}
    payload['dimension_types'] = {**payload['dimension_types'], 'status': 'date'}
    with pytest.raises(CollectorError, match='must be a date'):
        PostgresCollector(data_dir=tmp_path).collect(PostgresSourceSelection(
            source_uri=PG_URL, table_name=name, cutoff_column='created_at', cutoff_value='2026-12-31',
            contract=validate_contract(payload)))
