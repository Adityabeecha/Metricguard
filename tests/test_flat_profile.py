"""Column values and match counts that help analysts write filters before a run."""
import csv

import pytest

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.flat import ingest_flat, validate_contract
from packages.metricguard_core.flat_profile import column_values, match_count

ROWS = [['R1', '1.00', 'paid', 'true'], ['R2', '2.00', 'paid', 'false'], ['R3', '3.00', 'refunded', 'false'],
        ['R4', '4.00', '', 'true'], ['R5', '5.00', 'pending', '']]


@pytest.fixture
def snapshot(tmp_path):
    contract = validate_contract({
        'version': 5, 'mode': 'flat_snapshot', 'name': 'fixture', 'row_key': 'k', 'measure': 'amount',
        'precision': 18, 'scale': 2, 'categories': ['status', 'converted'], 'measure_meaning': 'Amount',
        'unit_label': 'INR', 'values_comparable': True, 'scope': 'entire_supplied_snapshot',
        'null_policy': 'blank_nullable', 'dimension_types': {'status': 'string', 'converted': 'boolean'},
        'nullable_categories': ['status', 'converted'], 'predicate_policy': 'flat-boolean-v1'})
    source = tmp_path / 'rows.csv'
    with source.open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows([['k', 'amount', 'status', 'converted'], *ROWS])
    database = tmp_path / 'rows.duckdb'
    ingest_flat(source, database, contract)
    return database, contract


def test_column_values_lists_every_value_by_frequency(snapshot):
    database, contract = snapshot
    result = column_values(database, contract, 'status')
    assert result['values'] == [{'value': 'paid', 'count': 2}, {'value': 'pending', 'count': 1}, {'value': 'refunded', 'count': 1}]
    assert result['complete'] is True and result['distinct_count'] == 3 and result['blank_count'] == 1
    assert [v['value'] for v in column_values(database, contract, 'converted')['values']] == ['false', 'true']


def test_column_values_marks_a_truncated_list_incomplete(snapshot):
    database, contract = snapshot
    result = column_values(database, contract, 'status', limit=1)
    assert result['complete'] is False and result['values'] == [{'value': 'paid', 'count': 2}]


def test_column_values_refuses_undeclared_columns(snapshot):
    database, contract = snapshot
    with pytest.raises(CoreError):
        column_values(database, contract, 'amount')


def test_match_count_uses_the_validated_where_clause(snapshot):
    database, contract = snapshot
    assert match_count(database, contract, "SELECT SUM(amount) FROM snapshot WHERE status = 'complete'") == {'matched': 0, 'total': 5}
    assert match_count(database, contract, "SELECT SUM(amount) FROM snapshot WHERE status IN ('paid', 'pending')") == {'matched': 3, 'total': 5}
    assert match_count(database, contract, 'SELECT COUNT(*) FROM snapshot') == {'matched': 5, 'total': 5}
    with pytest.raises(CoreError):
        match_count(database, contract, 'SELECT SUM(amount) FROM snapshot WHERE region = 1; DROP TABLE snapshot')



def test_concurrent_look_ups_on_one_snapshot_all_succeed(snapshot):
    """Current and proposed counts are requested together; neither may fail on DuckDB's locked configuration."""
    from concurrent.futures import ThreadPoolExecutor
    database, contract = snapshot
    jobs = [lambda: match_count(database, contract, "SELECT SUM(amount) FROM snapshot WHERE status = 'paid'"),
            lambda: column_values(database, contract, 'status')] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [f.result() for f in [pool.submit(job) for job in jobs]]
    assert results[0] == {'matched': 2, 'total': 5}
    assert all(r for r in results)

def test_http_column_values_and_match_count(tmp_path):
    from fastapi.testclient import TestClient
    from services.api.app import create_app
    from services.api.store import Store
    from services.supervisor.worker import Supervisor
    from tests.test_flat_workflow import InProcessLauncher

    url = f"sqlite:///{tmp_path / 'app.db'}"
    worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'profile-test-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/flat/contracts', json={
            'version': 4, 'mode': 'flat_snapshot', 'dimension_types': {'status': 'string'}, 'name': 'Orders',
            'row_key': 'row_id', 'measure': 'amount', 'precision': 18, 'scale': 2, 'categories': ['status'],
            'measure_meaning': 'Order amount', 'unit_label': 'INR', 'values_comparable': True,
            'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable'}).json()
        source = tmp_path / 'orders.csv'
        with source.open('w', newline='') as stream:
            csv.writer(stream).writerows([['row_id', 'amount', 'status'], ['R1', '1.00', 'paid'], ['R2', '2.00', 'paid'], ['R3', '3.00', 'cancelled']])
        with source.open('rb') as content:
            uploaded = client.post('/api/v1/flat/snapshots', files={'file': ('orders.csv', content, 'text/csv')},
                                   data={'contract_id': contract['id'], 'name': 'Orders'})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        snapshot = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()['snapshot_id']

        values = client.get(f'/api/v1/snapshots/{snapshot}/column-values', params={'column': 'status'})
        assert values.status_code == 200, values.text
        assert values.json()['values'] == [{'value': 'paid', 'count': 2}, {'value': 'cancelled', 'count': 1}]
        assert client.get(f'/api/v1/snapshots/{snapshot}/column-values', params={'column': 'amount'}).status_code == 422

        empty = client.post(f'/api/v1/snapshots/{snapshot}/match-count',
                            json={'raw_sql': "SELECT SUM(amount) FROM snapshot WHERE status = 'complete'"})
        assert empty.status_code == 200, empty.text
        assert empty.json()['counts'] == {'matched': 0, 'total': 3}
        refused = client.post(f'/api/v1/snapshots/{snapshot}/match-count', json={'raw_sql': 'DELETE FROM snapshot'})
        assert refused.status_code == 422
