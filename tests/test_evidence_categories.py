"""Change categories behind the reconciliation bridge and the records ledger: exact and complete."""
import hashlib
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from scripts.seed_demo import seed
from services.api import evidence_cache
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import run_job
from services.supervisor.worker import Supervisor

ROWS = [
    {'order_id': 'A', 'baseline_included': True, 'target_included': False, 'delta': '-200.00'},
    {'order_id': 'B', 'baseline_included': True, 'target_included': True, 'delta': '0.00'},
    {'order_id': 'C', 'baseline_included': False, 'target_included': True, 'delta': '75.50'},
    {'order_id': 'D', 'baseline_included': True, 'target_included': True, 'delta': '-0.25'},
    {'order_id': 'E', 'baseline_included': True, 'target_included': False, 'delta': '-90.00'},
    {'order_id': 'F', 'baseline_included': False, 'target_included': False, 'delta': '0'},
]
TWO_LOADS = [
    {'order_id': 'A', 'change': 'removed', 'baseline_included': True, 'target_included': False, 'delta': '-10'},
    {'order_id': 'B', 'change': 'added', 'baseline_included': False, 'target_included': True, 'delta': '4'},
    {'order_id': 'C', 'change': 'unchanged', 'baseline_included': True, 'target_included': True, 'delta': '0'},
    {'order_id': 'D', 'change': 'left_filter', 'baseline_included': True, 'target_included': False, 'delta': '-1'},
]


def artifact(tmp_path, rows, name='evidence.jsonl'):
    path = tmp_path / name
    path.write_bytes(b''.join(json.dumps(r).encode() + b'\n' for r in rows))
    return path, ('test', name, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture(params=['memory', 'disk'])
def index_kind(request, monkeypatch):
    evidence_cache.clear()
    if request.param == 'disk':
        monkeypatch.setattr(evidence_cache, 'DISK_INDEX_BYTES', 1)  # force the on-disk index
    yield request.param
    evidence_cache.clear()


def test_categories_are_exact_and_add_up_to_the_change(tmp_path, index_kind):
    path, key = artifact(tmp_path, ROWS)
    summary = evidence_cache.summary(path, key, lambda: None)
    assert summary['categories'] == [
        {'key': 'excluded', 'records': 2, 'change': '-290.00'},
        {'key': 'added', 'records': 1, 'change': '75.50'},
        {'key': 'value_changed', 'records': 1, 'change': '-0.25'},
    ]
    total = sum(Decimal(r['delta']) for r in ROWS)
    assert sum(Decimal(c['change']) for c in summary['categories']) == total
    assert sum(c['records'] for c in summary['categories']) == summary['changed']


def test_page_filters_by_category(tmp_path, index_kind):
    path, key = artifact(tmp_path, ROWS)
    rows, total = evidence_cache.page(path, key, lambda: None, 0, 10, category='excluded', order='impact')
    assert total == 2 and [r['order_id'] for r in rows] == ['A', 'E']
    rows, total = evidence_cache.page(path, key, lambda: None, 0, 10, category='added')
    assert total == 1 and rows[0]['order_id'] == 'C'
    rows, total = evidence_cache.page(path, key, lambda: None, 0, 10)
    assert total == len(ROWS)


def test_two_load_runs_keep_their_own_labels(tmp_path, index_kind):
    path, key = artifact(tmp_path, TWO_LOADS, 'loads.jsonl')
    summary = evidence_cache.summary(path, key, lambda: None)
    assert [(c['key'], c['records'], c['change']) for c in summary['categories']] == [
        ('added', 1, '4'), ('removed', 1, '-10'), ('left_filter', 1, '-1')]


def test_breakdown_reports_record_totals_for_share_bars(tmp_path):
    evidence_cache.clear()
    path, key = artifact(tmp_path, ROWS)
    context = {'A': {'region': 'north'}, 'B': {'region': 'north'}, 'C': {'region': 'south'},
               'D': {'region': 'east'}, 'E': {'region': 'west'}, 'F': {'region': 'west'}}
    split = evidence_cache.breakdown(path, key, lambda: None, 'region', context, limit=2)
    assert split['total_records'] == 6
    assert split['other_records'] == 2 and split['other_groups'] == 2  # south (1) and east (1)
    assert Decimal(split['abs_change_total']) == sum(abs(Decimal(r['delta'])) for r in ROWS)
    assert Decimal(split['other_change']) + sum(Decimal(g['change']) for g in split['groups']) == Decimal(split['total_change'])


def test_api_categories_reconcile_with_the_result(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='isolated')
    with TestClient(app) as client:
        boot = client.get('/api/v1/bootstrap').json()
        context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
                   'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
                   'scope': 'synthetic_retail_all_orders'}
        body = {'baseline_version_id': boot['definitions'][0]['versions'][0]['id'],
                'target_version_id': boot['definitions'][2]['versions'][0]['id'],
                'snapshot_id': boot['snapshots'][0]['id'], 'context': context, 'relationship': 'expected_agreement'}
        queued = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'categories'}).json()

        class Done:
            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        class InProcess:
            def start(self, input_dir, output_dir, command=None):
                run_job(input_dir, output_dir)
                return Done()

        assert Supervisor(Store(url), InProcess(), tmp_path / 'jobs', 'categories', data_dir=tmp_path).run_once()
        run = client.get(f"/api/v1/comparisons/{queued['id']}").json()
        summary = client.get(f"/api/v1/comparisons/{queued['id']}/evidence-summary").json()
        listed = sum(Decimal(c['change']) for c in summary['categories'])
        assert listed + Decimal(run['result']['residual']) == Decimal(run['result']['delta_target_minus_baseline'])
        first = summary['categories'][0]
        page = client.get(f"/api/v1/comparisons/{queued['id']}/evidence",
                          params={'category': first['key'], 'limit': 100}).json()
        assert page['total'] == first['records']
        assert client.get(f"/api/v1/comparisons/{queued['id']}/evidence", params={'category': 'bogus'}).status_code == 422
