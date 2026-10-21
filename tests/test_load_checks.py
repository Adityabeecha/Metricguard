"""New-load checks reuse approved SQL and the ordinary comparison admission path."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.api.store import DomainError


def fixture_store():
    snapshots = {
        'old': {'id': 'old', 'project_id': 'p', 'name': 'Earlier', 'created_at': '2026-01-01',
                'execution_policy': 'flat_snapshot_v1', 'contract_hash': 'a' * 64,
                'scope': 'entire_supplied_snapshot', 'manifest': {'unit_label': 'INR', 'manifest_hash': 'old'}},
        'new': {'id': 'new', 'project_id': 'p', 'name': 'Later', 'created_at': '2026-02-01',
                'execution_policy': 'flat_snapshot_v1', 'contract_hash': 'a' * 64,
                'scope': 'entire_supplied_snapshot', 'manifest': {'unit_label': 'INR', 'manifest_hash': 'new'}},
    }
    versions = {'v': {'id': 'v', 'project_id': 'p', 'definition_id': 'd', 'contract_hash': 'a' * 64,
                       'execution_policy': 'flat_snapshot_v1', 'support_state': 'executable', 'raw_sql': 'SELECT SUM(amount) FROM snapshot'}}
    metrics = [{'id': 'm', 'display_name': 'Revenue', 'active_binding': {'definition_id': 'd', 'approved_version_id': 'v'}}]
    store = SimpleNamespace(snapshot=lambda ident: snapshots[ident], version=lambda ident: versions[ident],
                            list_metrics=lambda project_id: deepcopy(metrics))
    return store, snapshots, versions, metrics


def validate(body, auth=None, pid=None):
    return {'baseline_version_id': body.version_id, 'target_version_id': body.version_id,
            'snapshot_id': body.baseline_snapshot_id, 'target_snapshot_id': body.target_snapshot_id,
            'context': {'comparison': 'snapshot_change'}, 'relationship': 'candidate'}


def test_preview_only_approved_same_dataset_and_lists_skip_reasons():
    from services.api.load_checks import preview_load_checks
    store, _, versions, metrics = fixture_store()
    versions['other'] = {**versions['v'], 'id': 'other', 'contract_hash': 'b' * 64}
    versions['legacy'] = {**versions['v'], 'id': 'legacy', 'contract_hash': None}
    metrics.extend([
        {'id': 'draft', 'display_name': 'Draft', 'active_binding': None},
        {'id': 'other', 'display_name': 'Elsewhere', 'active_binding': {'definition_id': 'd', 'approved_version_id': 'other'}},
        {'id': 'legacy', 'display_name': 'Old SQL', 'active_binding': {'definition_id': 'd', 'approved_version_id': 'legacy'}},
    ])
    result = preview_load_checks(store, 'p', 'old', 'new', validate)
    assert [(item['metric_id'], item['version_id']) for item in result['eligible']] == [('m', 'v')]
    assert {item['metric_id']: item['reason_code'] for item in result['skipped']} == {
        'draft': 'NO_APPROVED_VERSION', 'other': 'DIFFERENT_DATASET', 'legacy': 'UNLINKED_SQL'}
    assert result['run_count'] == 1


def test_preview_reports_unsupported_approved_sql_without_queueing():
    from services.api.load_checks import preview_load_checks
    store, _, _, _ = fixture_store()
    def unsupported(*args, **kwargs):
        raise DomainError('NOT_COMPARABLE', 'Ratios are not supported between loads.', 422)
    result = preview_load_checks(store, 'p', 'old', 'new', unsupported)
    assert result['eligible'] == []
    assert result['skipped'][0]['reason'] == 'Ratios are not supported between loads.'


@pytest.mark.parametrize('change', ['foreign', 'reverse', 'different', 'same'])
def test_preview_refuses_foreign_or_invalid_load_pairs(change):
    from services.api.load_checks import preview_load_checks
    store, snapshots, _, _ = fixture_store()
    old, new = 'old', 'new'
    if change == 'foreign': snapshots['new']['project_id'] = 'elsewhere'
    if change == 'different': snapshots['new']['contract_hash'] = 'b' * 64
    if change == 'reverse': old, new = new, old
    if change == 'same': new = old
    with pytest.raises(DomainError):
        preview_load_checks(store, 'p', old, new, validate)


def test_start_freezes_metric_label_and_delegates_limit_admission():
    from services.api.load_checks import start_load_check
    store, _, _, _ = fixture_store()
    seen = []
    def queue(payload, key, mode, project_id):
        seen.append((payload, key, mode, project_id))
        raise DomainError('FREE_TIER_DAILY_RUNS', 'Daily allowance exhausted.', 429)
    store.queue = queue
    with pytest.raises(DomainError) as error:
        start_load_check(store, 'p', 'old', 'new', 'm', 'v', validate, 'isolated', 'key')
    assert error.value.code == 'FREE_TIER_DAILY_RUNS'
    payload, key, mode, pid = seen[0]
    assert payload['load_check'] == {'metric_id': 'm', 'metric_name': 'Revenue', 'version_id': 'v'}
    assert payload['purpose'] == 'snapshot_change'
    assert (key, mode, pid) == ('key', 'isolated', 'p')


def test_start_refuses_stale_approval_and_demo_mode():
    from services.api.load_checks import start_load_check
    store, _, _, _ = fixture_store()
    store.queue = lambda *a, **kw: pytest.fail('must not enqueue')
    for version, mode in [('stale', 'isolated'), ('v', 'demo')]:
        with pytest.raises(DomainError):
            start_load_check(store, 'p', 'old', 'new', 'm', version, validate, mode, 'key')


def test_http_preview_execute_history_quota_and_reader_authorization(tmp_path, monkeypatch):
    import csv
    from fastapi.testclient import TestClient
    from services.api.app import create_app
    from services.api.store import Store
    from services.supervisor.worker import Supervisor
    from tests.test_distinct_and_ratio import _InProcess
    from tests.test_item_management import ROWS, _setup

    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    worker = Supervisor(store, _InProcess(), tmp_path / 'jobs', 'load-check-worker')
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        old_id, initial_run = _setup(tmp_path, client, worker)
        bootstrap = client.get('/api/v1/bootstrap').json()
        pid = bootstrap['project']['id']
        version_id = initial_run['target_version_id']
        definition = next(item for item in bootstrap['definitions'] if any(version['id'] == version_id for version in item['versions']))
        metric = client.post(f'/api/v1/projects/{pid}/metrics', json={'display_name': 'Paid revenue'}).json()
        metric_id = metric['id']
        bound = client.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/bindings', json={
            'definition_id': definition['id'], 'status': 'candidate', 'approved_version_id': version_id})
        assert bound.status_code == 201, bound.text
        promoted = client.post(f'/api/v1/projects/{pid}/metrics/{metric_id}/promote', json={
            'definition_id': definition['id'], 'approved_version_id': version_id, 'comparison_run_id': initial_run['id']})
        assert promoted.status_code == 200, promoted.text
        # An unapproved metric is explicitly skipped in the preview.
        draft = client.post(f'/api/v1/projects/{pid}/metrics', json={'display_name': 'Unapproved'}).json()
        contract_id = bootstrap['flat_contracts'][0]['id']
        source = tmp_path / 'new.csv'
        with source.open('w', newline='', encoding='utf-8') as stream:
            csv.writer(stream).writerows([*ROWS, ['D', '7.50', 'paid']])
        with source.open('rb') as content:
            uploaded = client.post('/api/v1/flat/snapshots', files={'file': ('new.csv', content, 'text/csv')},
                data={'contract_id': contract_id, 'name': 'New orders'})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        new_id = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()['snapshot_id']
        body = {'baseline_snapshot_id': old_id, 'target_snapshot_id': new_id}
        path = f'/api/v1/projects/{pid}/load-checks'
        monkeypatch.setenv('METRICGUARD_MAX_RUNS_PER_DAY', '2')
        preview = client.post(f'{path}/preview', json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()['run_count'] == 1
        assert preview.json()['runs_available'] == 1
        assert preview.json()['eligible'][0]['version_id'] == version_id
        assert preview.json()['skipped'][0]['metric_id'] == draft['id']
        assert preview.json()['skipped'][0]['reason_code'] == 'NO_APPROVED_VERSION'
        queued = client.post(f'{path}/start', json={**body, 'metric_id': metric_id, 'version_id': version_id},
            headers={'Idempotency-Key': 'new-load-check'})
        assert queued.status_code == 202, queued.text
        assert queued.json()['load_check']['metric_name'] == 'Paid revenue'
        # Retrying the identical admission consumes no extra daily allowance.
        retry = client.post(f'{path}/start', json={**body, 'metric_id': metric_id, 'version_id': version_id},
            headers={'Idempotency-Key': 'new-load-check'})
        assert retry.status_code == 202 and retry.json()['id'] == queued.json()['id']
        assert worker.run_once()
        result = client.get(f"/api/v1/comparisons/{queued.json()['id']}").json()
        assert result['state'] == 'succeeded', result.get('error')
        assert result['result']['baseline_value'] == '12.50'
        assert result['result']['target_value'] == '20.00'
        assert result['result']['delta_target_minus_baseline'] == '7.50'
        from decimal import Decimal
        assert Decimal(result['result']['residual']) == 0
        history = client.get(path)
        assert history.status_code == 200, history.text
        assert [item['id'] for item in history.json()['items']] == [queued.json()['id']]
        assert history.json()['items'][0]['load_check']['metric_name'] == 'Paid revenue'
        assert history.json()['items'][0]['result']['delta_target_minus_baseline'] == '7.50'
        limited = client.post(f'{path}/start', json={**body, 'metric_id': metric_id, 'version_id': version_id},
            headers={'Idempotency-Key': 'over-limit'})
        assert limited.status_code == 429 and limited.json()['error']['code'] == 'FREE_TIER_DAILY_RUNS'
        reader, key = store.create_user('Reader', 'load-reader@example.com')
        store.add_project_member(pid, reader['id'], 'reader')
        headers = {'X-API-Key': key, 'X-Project-Id': pid, 'Idempotency-Key': 'reader-check'}
        assert client.post(f'{path}/preview', json=body, headers=headers).status_code == 200
        assert client.get(path, headers=headers).status_code == 200
        denied = client.post(f'{path}/start', json={**body, 'metric_id': metric_id, 'version_id': version_id}, headers=headers)
        assert denied.status_code == 403, denied.text
        mismatch = client.post(f'{path}/preview', json=body, headers={'X-Project-Id': 'foreign'})
        assert mismatch.status_code == 400, mismatch.text
