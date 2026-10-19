"""Snapshots recorded by the PostgreSQL collector must be comparable like uploaded files.

Before 2026-10-18 `record_snapshot` stored single-table warehouse snapshots without
their execution policy, so every comparison on them was refused as a policy mismatch.
"""
import csv

from packages.metricguard_core.flat import ingest_flat, validate_contract
from services.api.store import Store


def _manifest(tmp_path):
    c = validate_contract({'version': 5, 'predicate_policy': 'flat-boolean-v1', 'mode': 'flat_snapshot', 'name': 'wh',
        'row_key': 'k', 'measure': 'amount', 'precision': 12, 'scale': 2, 'categories': ['status'],
        'dimension_types': {'status': 'string'}, 'nullable_categories': ['status'], 'measure_meaning': 'm',
        'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'})
    source = tmp_path / 'wh.csv'
    with source.open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows([['k', 'amount', 'status'], ['A', '1.00', 'paid'], ['B', '2.00', 'void']])
    database = tmp_path / 'wh.duckdb'
    return ingest_flat(source, database, c), database


def test_collected_flat_snapshot_records_its_policy(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    manifest, database = _manifest(tmp_path)
    recorded = store.record_snapshot('warehouse extract', manifest, database)
    assert recorded['execution_policy'] == 'flat_snapshot_v1'
    assert store.snapshot(recorded['id'])['execution_policy'] == 'flat_snapshot_v1'


def test_older_collected_snapshot_reads_back_with_its_manifest_policy(tmp_path):
    from sqlalchemy import update
    from services.api.store import snapshots
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    manifest, database = _manifest(tmp_path)
    recorded = store.record_snapshot('warehouse extract', manifest, database)
    legacy = {k: v for k, v in recorded.items() if k != 'execution_policy'}
    with store.engine.begin() as conn:
        conn.execute(update(snapshots).where(snapshots.c.id == recorded['id']).values(data=legacy))
    assert store.snapshot(recorded['id'])['execution_policy'] == 'flat_snapshot_v1'
    assert any(s.get('execution_policy') == 'flat_snapshot_v1' for s in store.bootstrap()['snapshots'])
