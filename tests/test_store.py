import time

import pytest


def make_store(tmp_path):
    from services.api.store import Store
    return Store(f"sqlite:///{tmp_path / 'app.db'}")


def test_idempotency_reuses_original_run_and_rejects_different_payload(tmp_path):
    from services.api.store import DomainError
    store = make_store(tmp_path)
    request = seed_request(store)
    first = store.queue(request, 'retry-key', 'isolated')
    assert store.queue(request, 'retry-key', 'isolated')['id'] == first['id']
    with pytest.raises(DomainError) as err:
        store.queue({**request, 'relationship': 'intentional_variant'}, 'retry-key', 'isolated')
    assert err.value.code == 'IDEMPOTENCY_CONFLICT'


def seed_request(store):
    # Public storage boundaries; immutable versions are built by the real parser.
    a = store.record_definition('A', 'Test', 'Local', "SELECT SUM(order_amount) FROM orders", 'order_date')
    b = store.record_definition('B', 'Test', 'Local', "SELECT SUM(order_amount - refund_amount) FROM orders", 'payment_date')
    snap = store.record_snapshot('Test', {'manifest_hash': 'm', 'source_hash': 's', 'row_count': 8}, 'test.duckdb')
    return {'baseline_version_id': a['versions'][0]['id'], 'target_version_id': b['versions'][0]['id'],
            'snapshot_id': snap['id'], 'context': {'start': '2026-08-01', 'end_exclusive': '2026-09-01'},
            'relationship': 'expected_agreement'}


def test_expired_lease_recovers_and_old_worker_cannot_finalize(tmp_path):
    store = make_store(tmp_path)
    queued = store.queue(seed_request(store), 'lease', 'isolated')
    first = store.claim('first', lease_seconds=-1)
    second = store.claim('second')
    assert first['id'] == second['id'] == queued['id']
    assert second['attempt'] == 2
    assert not store.finish(queued['id'], 'first', 1, error={'code': 'LATE'})
    assert store.finish(queued['id'], 'second', 2, error={'code': 'EXPECTED_FAILURE'})
    assert store.get_run(queued['id'])['state'] == 'failed'


def test_cancel_queued_and_running_jobs_cannot_become_successful(tmp_path):
    store = make_store(tmp_path)
    request = seed_request(store)
    first = store.queue(request, 'cancel-queued', 'isolated')
    assert store.cancel(first['id'])['state'] == 'canceled'
    assert store.claim('worker') is None
    second = store.queue(request, 'cancel-running', 'isolated')
    claimed = store.claim('worker')
    assert store.cancel(second['id'])['state'] == 'canceled'
    assert not store.finish(second['id'], 'worker', claimed['attempt'], result={'explanation_status': 'complete_ordered_bridge'})
    assert store.get_run(second['id'])['state'] == 'canceled'


def test_cancel_after_worker_death_is_terminal_without_recovery_worker(tmp_path):
    store = make_store(tmp_path)
    run = store.queue(seed_request(store), 'dead-worker-cancel', 'isolated')
    store.claim('dead', lease_seconds=-1)
    assert store.cancel(run['id'])['state'] == 'canceled'
    assert store.claim('replacement') is None


def test_versions_are_immutable_and_cosmetic_edits_do_not_mark_structural_change(tmp_path):
    store = make_store(tmp_path)
    a = store.record_definition('A', 'Test', 'Local', 'SELECT SUM(order_amount) FROM orders', 'order_date')
    original = a['versions'][0]
    changed = store.record_definition('A', 'Test', 'Local', 'SELECT SUM(order_amount) AS sales FROM orders', 'order_date', a['id'])
    assert len(changed['versions']) == 2
    assert changed['versions'][1]['change_kind'] == 'cosmetic'
    assert store.version(original['id']) == original
    repeated = store.record_definition('A', 'Test', 'Local', changed['versions'][1]['raw_sql'], 'order_date', a['id'])
    assert len(repeated['versions']) == 2


def test_definition_without_confirmed_time_column_needs_context(tmp_path):
    store = make_store(tmp_path)
    imported = store.record_definition('Undated', 'Test', 'Local', 'SELECT SUM(order_amount) FROM orders')
    version = imported['versions'][0]
    assert version['support_state'] == 'needs_context'
    assert 'date column' in version['support_reason'].lower()


def test_baseline_decision_supersedes_only_matching_scope_and_keeps_history(tmp_path):
    store = make_store(tmp_path)
    request = seed_request(store)
    data = {'version_id': request['baseline_version_id'], 'scope': 'August reporting', 'reason': 'Reviewed',
            'actor_label': 'Analyst', 'effective_from': '2026-08-01'}
    first = store.record_baseline(data)
    second = store.record_baseline({**data, 'version_id': request['target_version_id']})
    records = store.bootstrap()['baselines']
    assert len(records) == 2
    assert next(r for r in records if r['id'] == first['id'])['superseded_by'] == second['id']
    assert second['superseded_by'] is None


def test_parallel_workers_never_claim_the_same_comparison(tmp_path):
    """scripts\mg.ps1 -Workers N starts N supervisors on one SQLite queue; each run is claimed once."""
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.exc import OperationalError
    from services.api.store import Store
    store = make_store(tmp_path)
    request = seed_request(store)
    queued = {store.queue(request, f'parallel-{i}', 'isolated')['id'] for i in range(40)}

    def worker(name):
        own = Store(f"sqlite:///{tmp_path / 'app.db'}")  # its own engine, like a separate process
        claimed = []
        while True:
            try:
                run = own.claim(name)
            except OperationalError:
                continue  # the real worker loop retries a briefly locked database
            if run is None:
                return claimed
            claimed.append(run['id'])

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(worker, ['w1', 'w2', 'w3', 'w4']))
    everything = [run for claimed in results for run in claimed]
    assert sorted(everything) == sorted(queued)  # every run claimed, none twice
