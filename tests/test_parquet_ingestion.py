from pathlib import Path

import duckdb
import pytest

from packages.metricguard_core import CoreError, compare, ingest_csv, ingest_parquet, inspect_snapshot, parse_definition


ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / 'MetricGuard_Planning_Pack/examples/orders_golden.csv'


def golden_parquet(tmp_path):
    csv_snapshot = tmp_path / 'source.duckdb'
    ingest_csv(CSV, csv_snapshot)
    parquet = tmp_path / 'orders.parquet'
    with duckdb.connect(str(csv_snapshot)) as con:
        con.execute('COPY orders TO ? (FORMAT PARQUET)', [str(parquet)])
    return parquet


def test_parquet_ingestion_preserves_golden_reconciliation(tmp_path):
    source = golden_parquet(tmp_path)
    destination = tmp_path / 'parquet.duckdb'
    manifest = ingest_parquet(source, destination)
    assert manifest['row_count'] == 8
    assert manifest['ingestion_version'] == 'parquet-v1'
    assert inspect_snapshot(destination)['manifest_hash'] == manifest['manifest_hash']

    examples = ROOT / 'MetricGuard_Planning_Pack/examples'
    baseline = parse_definition((examples / 'dashboard_a.sql').read_text(), time_column='order_date')
    target = parse_definition((examples / 'finance.sql').read_text(), time_column='payment_date')
    context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
               'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
               'scope': 'synthetic_retail_all_orders'}
    result = compare(destination, baseline, target, context)
    assert result['delta_target_minus_baseline'] == '-6000000.00'
    assert result['residual'] == '0.00'


def test_parquet_rejects_duplicate_order_ids_without_publishing_snapshot(tmp_path):
    source = golden_parquet(tmp_path)
    duplicate = tmp_path / 'duplicate.parquet'
    with duckdb.connect() as con:
        con.execute('COPY (SELECT * FROM read_parquet(?) UNION ALL SELECT * FROM read_parquet(?) LIMIT 9) TO ? (FORMAT PARQUET)',
                    [str(duplicate), str(source), str(source)])
    destination = tmp_path / 'duplicate.duckdb'
    with pytest.raises(CoreError) as error:
        ingest_parquet(duplicate, destination)
    assert error.value.code == 'invalid_row'
    assert not destination.exists()


def test_parquet_rejects_schema_mismatch_and_invalid_bytes(tmp_path):
    source = golden_parquet(tmp_path)
    missing = tmp_path / 'missing.parquet'
    with duckdb.connect() as con:
        con.execute('COPY (SELECT * EXCLUDE currency FROM read_parquet(?)) TO ? (FORMAT PARQUET)',
                    [str(missing), str(source)])
    with pytest.raises(CoreError) as error:
        ingest_parquet(missing, tmp_path / 'missing.duckdb')
    assert error.value.code == 'invalid_schema'
    damaged = tmp_path / 'damaged.parquet'
    damaged.write_bytes(b'not a parquet file')
    with pytest.raises(CoreError) as error:
        ingest_parquet(damaged, tmp_path / 'damaged.duckdb')
    assert error.value.code == 'invalid_parquet'


def test_parquet_rejects_incompatible_currency(tmp_path):
    source = golden_parquet(tmp_path)
    wrong_currency = tmp_path / 'usd.parquet'
    with duckdb.connect() as con:
        con.execute("COPY (SELECT * REPLACE ('USD' AS currency) FROM read_parquet(?)) TO ? (FORMAT PARQUET)",
                    [str(wrong_currency), str(source)])
    destination = tmp_path / 'usd.duckdb'
    with pytest.raises(CoreError) as error:
        ingest_parquet(wrong_currency, destination)
    assert error.value.code == 'invalid_row'
    assert not destination.exists()
