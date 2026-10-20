"""Live-user import regressions: labels, inherited units and calculation capability."""
import csv

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from packages.metricguard_core.flat import compare_flat, ingest_flat, parse_flat, validate_contract
from services.api.app import create_app
from services.api.store import Store, snapshots


def _contract():
    return {'version': 5, 'mode': 'flat_snapshot', 'name': 'Orders', 'row_key': 'id',
        'measure': 'amount', 'precision': 18, 'scale': 2, 'categories': ['status'],
        'dimension_types': {'status': 'string'}, 'nullable_categories': [],
        'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
        'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable',
        'predicate_policy': 'flat-boolean-v1'}


def _source(path):
    with path.open('w', encoding='utf-8', newline='') as output:
        csv.writer(output).writerows([['id', 'amount', 'status'], ['a', '10.00', 'paid'],
                                     ['b', '30.00', 'paid'], ['c', '100.00', 'void']])
    return path


@pytest.mark.parametrize('label,expected', [('October 2026', 'October 2026'),
                                           ('  October 2026  ', 'October 2026'),
                                           ('', 'orders_october.csv'), ('   ', 'orders_october.csv')])
def test_newer_load_keeps_entered_label_and_inherited_unit(tmp_path, label, expected):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    app = create_app(database_url=url, data_dir=tmp_path, mode='isolated')
    store = Store(url)
    source = _source(tmp_path / 'orders_october.csv')
    with TestClient(app) as client:
        registered = client.post('/api/v1/flat/contracts', json=_contract())
        assert registered.status_code == 201, registered.text
        contract = registered.json()
        with source.open('rb') as contents:
            uploaded = client.post('/api/v1/flat/snapshots',
                files={'file': ('orders_october.csv', contents, 'text/csv')},
                data={'contract_id': contract['id'], 'name': label})
        assert uploaded.status_code == 202, uploaded.text
        queued = uploaded.json()
        assert queued['name'] == expected
        job = store.claim_import('regression-worker')
        destination = tmp_path / 'loaded.duckdb'
        manifest = ingest_flat(source, destination, validate_contract(_contract()))
        assert store.finish_import(job['id'], 'regression-worker', job['attempt'], manifest=manifest, artifact_path=destination)
        completed = store.get_import(queued['id'])
        stored = store.snapshot(completed['snapshot_id'])
        assert stored['name'] == expected
        assert stored['unit_label'] == stored['manifest']['unit_label'] == 'INR'
        with store.engine.connect() as connection:
            raw = connection.execute(select(snapshots.c.data).where(snapshots.c.id == stored['id'])).scalar_one()
        assert raw['unit_label'] == 'INR'  # persisted, not only repaired in the API response
        bootstrap = client.get('/api/v1/bootstrap').json()
        public = next(snapshot for snapshot in bootstrap['snapshots'] if snapshot['id'] == stored['id'])
        assert public['name'] == expected and public['unit_label'] == 'INR'


def test_older_import_summary_recovers_the_manifest_unit(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    source = _source(tmp_path / 'source.csv')
    database = tmp_path / 'source.duckdb'
    manifest = ingest_flat(source, database, _contract())
    snapshot = store.record_snapshot('October', manifest, database)
    without_unit = {key: value for key, value in snapshot.items() if key != 'unit_label'}
    with store.engine.begin() as connection:
        connection.execute(update(snapshots).where(snapshots.c.id == snapshot['id']).values(data=without_unit))
    assert store.snapshot(snapshot['id'])['unit_label'] == 'INR'
    assert store.bootstrap()['snapshots'][0]['unit_label'] == 'INR'


@pytest.mark.parametrize('expression', ['AVG(amount)', 'SUM(amount) / COUNT(amount)'])
def test_default_compound_csv_contract_accepts_average_and_ratio(tmp_path, expression):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        response = client.post('/api/v1/flat/contracts', json=_contract())
        assert response.status_code == 201, response.text
        contract = response.json()
        sql = f"SELECT {expression} FROM snapshot WHERE status = 'paid' AND NOT (status = 'void' OR status = 'held')"
        registered = client.post('/api/v1/flat/definitions', json={
            'name': expression, 'contract_id': contract['id'], 'raw_sql': sql})
        assert registered.status_code == 201, registered.text
        source = _source(tmp_path / 'source.csv')
        database = tmp_path / 'source.duckdb'
        manifest = ingest_flat(source, database, _contract())
        ir = parse_flat(sql, _contract())
        context = {'mode': 'flat_snapshot', 'scope': contract['scope'], 'contract_hash': contract['contract_hash'],
                   'unit_label': 'INR', 'values_comparable': True, 'manifest_hash': manifest['manifest_hash']}
        result = compare_flat(database, ir, ir, context)
        assert result['baseline_value'] == result['target_value'] == '20.000000'
        assert result['residual'] == '0'


def test_saved_calculations_are_separate_from_governed_metrics(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    contract = store.record_flat_contract(_contract())
    for label, sql in [('All orders', 'SELECT SUM(amount) FROM snapshot'),
                       ('Paid orders', "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'")]:
        store.record_flat_definition(name=label, raw_sql=sql, contract_id=contract['id'])
    bootstrap = store.bootstrap()
    assert len(bootstrap['definitions']) == 2
    assert bootstrap['metrics'] == []
