"""V3 Multi-Role Authorization & Project Isolation Tests.

Fixtures:
- Owner A (Project A)
- Analyst A (Project A)
- Reader A (Project A)
- Analyst B (Project B)

Verifies:
- Access isolation: Cross-project access attempts fail (403).
- Role permissions: Reader cannot write/run; Analyst cannot record decisions; Owner can administer.
- Revocation: Revoked members immediately lose access to all project objects & direct downloads.
- Backward compatibility: Unverified local analyst only has access to default local-retail.
"""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from scripts.seed_demo import seed


@pytest.fixture
def v3_setup(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    store = app.state.store

    # 1. Create users
    owner_a, owner_a_key = store.create_user('Owner A', 'owner_a@metricguard.internal')
    analyst_a, analyst_a_key = store.create_user('Analyst A', 'analyst_a@metricguard.internal')
    reader_a, reader_a_key = store.create_user('Reader A', 'reader_a@metricguard.internal')
    analyst_b, analyst_b_key = store.create_user('Analyst B', 'analyst_b@metricguard.internal')

    # 2. Create projects & memberships
    proj_a = store.create_project('Project Alpha', owner_id=owner_a['id'], project_id='proj-alpha')
    store.add_project_member('proj-alpha', analyst_a['id'], 'analyst')
    store.add_project_member('proj-alpha', reader_a['id'], 'reader')

    proj_b = store.create_project('Project Beta', owner_id=analyst_b['id'], project_id='proj-beta')

    # 3. Create sample objects in Project A
    def_a = store.record_definition(
        name='Revenue A', source_label='Warehouse', owner_label='Finance',
        raw_sql='SELECT SUM(order_amount) FROM orders', time_column='order_date',
        project_id='proj-alpha'
    )
    snap_a = store.bootstrap('local-retail')['snapshots'][0]
    # Re-register snapshot under proj-alpha
    snap_proj_a = store.record_snapshot(
        name='Golden Snapshot A', manifest=snap_a['manifest'],
        artifact_path=snap_a['artifact_path'], project_id='proj-alpha'
    )

    # Queue and complete a comparison run in Project A
    context = {
        'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
        'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
        'scope': 'synthetic_retail_all_orders'
    }
    run_req = {
        'baseline_version_id': def_a['versions'][0]['id'],
        'target_version_id': def_a['versions'][0]['id'],
        'snapshot_id': snap_proj_a['id'],
        'context': context,
        'relationship': 'expected_agreement'
    }
    run_a = store.queue(run_req, idempotency_key='run-proj-a', execution_mode='demo', project_id='proj-alpha')
    demo_res = {
        'baseline_value': '1000.00', 'target_value': '1000.00', 'delta_target_minus_baseline': '0.00',
        'explanation_status': 'complete_ordered_bridge', 'residual': '0.00', 'evidence': [],
        'provenance': {'baseline_source_hash': 'h1', 'target_source_hash': 'h2', 'context_fingerprint': 'c1',
                       'snapshot_manifest_hash': snap_proj_a['manifest_hash']}
    }
    run_a = store.complete_demo(run_a['id'], demo_res)

    # Create sample objects in Project B
    def_b = store.record_definition(
        name='Revenue B', source_label='Warehouse B', owner_label='Finance B',
        raw_sql='SELECT SUM(order_amount) FROM orders', time_column='order_date',
        project_id='proj-beta'
    )

    with TestClient(app) as client:
        yield {
            'client': client,
            'store': store,
            'tokens': {
                'owner_a': owner_a_key,
                'analyst_a': analyst_a_key,
                'reader_a': reader_a_key,
                'analyst_b': analyst_b_key,
            },
            'users': {
                'owner_a': owner_a,
                'analyst_a': analyst_a,
                'reader_a': reader_a,
                'analyst_b': analyst_b,
            },
            'proj_a': proj_a,
            'proj_b': proj_b,
            'def_a': def_a,
            'def_b': def_b,
            'snap_a': snap_proj_a,
            'run_a': run_a,
        }


def test_cross_project_access_denied(v3_setup):
    """Analyst B in Project Beta cannot access any Project Alpha resources."""
    client = v3_setup['client']
    b_headers = {'Authorization': f"Bearer {v3_setup['tokens']['analyst_b']}"}
    run_a_id = v3_setup['run_a']['id']
    def_a_id = v3_setup['def_a']['id']
    snap_a_id = v3_setup['snap_a']['id']

    # 1. Cannot access definition of Project A
    res = client.get(f"/api/v1/definitions/{def_a_id}", headers=b_headers)
    assert res.status_code == 403

    # 2. Cannot access snapshot of Project A
    res = client.get(f"/api/v1/snapshots/{snap_a_id}", headers=b_headers)
    assert res.status_code == 403

    # 3. Cannot access run of Project A
    res = client.get(f"/api/v1/comparisons/{run_a_id}", headers=b_headers)
    assert res.status_code == 403

    # 4. Cannot access evidence of Project A
    res = client.get(f"/api/v1/comparisons/{run_a_id}/evidence", headers=b_headers)
    assert res.status_code == 403

    # 5. Cannot export comparison of Project A
    res = client.get(f"/api/v1/comparisons/{run_a_id}/export?format=json", headers=b_headers)
    assert res.status_code == 403

    # 6. Cannot cancel run of Project A
    res = client.post(f"/api/v1/comparisons/{run_a_id}/cancel", headers=b_headers)
    assert res.status_code == 403

    # 7. Cannot bootstrap Project A
    res = client.get("/api/v1/bootstrap", headers={**b_headers, 'X-Project-Id': 'proj-alpha'})
    assert res.status_code == 403


def test_reader_role_permissions(v3_setup):
    """Reader A can view Project Alpha objects but cannot perform write/run actions."""
    client = v3_setup['client']
    r_headers = {'Authorization': f"Bearer {v3_setup['tokens']['reader_a']}", 'X-Project-Id': 'proj-alpha'}
    run_a_id = v3_setup['run_a']['id']
    def_a_id = v3_setup['def_a']['id']

    # 1. Reader CAN view bootstrap, definitions, runs, and export
    res = client.get("/api/v1/bootstrap", headers=r_headers)
    assert res.status_code == 200
    assert len(res.json()['definitions']) >= 1

    res = client.get(f"/api/v1/definitions/{def_a_id}", headers=r_headers)
    assert res.status_code == 200

    res = client.get(f"/api/v1/comparisons/{run_a_id}", headers=r_headers)
    assert res.status_code == 200

    res = client.get(f"/api/v1/comparisons/{run_a_id}/export?format=json", headers=r_headers)
    assert res.status_code == 200

    # 2. Reader CANNOT create definitions
    res = client.post("/api/v1/definitions", headers=r_headers, json={
        'name': 'Reader attempt', 'source_label': 'Test', 'owner_label': 'Reader',
        'raw_sql': 'SELECT SUM(order_amount) FROM orders', 'time_column': 'order_date'
    })
    assert res.status_code == 403

    # 3. Reader CANNOT run comparisons
    res = client.post("/api/v1/comparisons", headers={**r_headers, 'Idempotency-Key': 'reader-run'}, json={
        'baseline_version_id': v3_setup['def_a']['versions'][0]['id'],
        'target_version_id': v3_setup['def_a']['versions'][0]['id'],
        'snapshot_id': v3_setup['snap_a']['id'],
        'context': {
            'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
            'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
            'scope': 'synthetic_retail_all_orders'
        },
        'relationship': 'expected_agreement'
    })
    assert res.status_code == 403

    # 4. Reader CANNOT save investigations
    res = client.post("/api/v1/investigations", headers=r_headers, json={
        'title': 'Reader investigation', 'run_id': run_a_id, 'decision': 'read_only', 'actor_label': 'Reader A'
    })
    assert res.status_code == 403


def test_analyst_cannot_record_decisions_or_manage_project(v3_setup):
    """Analyst A can run comparisons and request reviews, but cannot record decisions or manage members."""
    client = v3_setup['client']
    a_headers = {'Authorization': f"Bearer {v3_setup['tokens']['analyst_a']}", 'X-Project-Id': 'proj-alpha'}
    run_a_id = v3_setup['run_a']['id']
    ver_a_id = v3_setup['def_a']['versions'][0]['id']

    # 1. Analyst CAN create review request
    rev_res = client.post("/api/v1/projects/proj-alpha/review-requests", headers=a_headers, json={
        'run_id': run_a_id,
        'rationale': 'Requesting signoff on revenue definition',
        'is_intentional_variant': False
    })
    assert rev_res.status_code == 201
    rev_id = rev_res.json()['id']

    # 2. Analyst CANNOT record review decision (Owner only)
    dec_res = client.post("/api/v1/projects/proj-alpha/decisions", headers=a_headers, json={
        'review_request_id': rev_id,
        'approved_version_id': ver_a_id,
        'decision': 'approved',
        'rationale': 'Analyst attempting to approve'
    })
    assert dec_res.status_code == 403

    # 3. Analyst CANNOT add or revoke project members
    mem_res = client.post("/api/v1/projects/proj-alpha/members", headers=a_headers, json={
        'user_id': v3_setup['users']['analyst_b']['id'], 'role': 'reader'
    })
    assert mem_res.status_code == 403

    # 4. Analyst CANNOT set quotas
    quota_res = client.post("/api/v1/projects/proj-alpha/quotas", headers=a_headers, json={
        'max_storage_bytes': 1000000
    })
    assert quota_res.status_code == 403


def test_membership_revocation_immediately_cuts_access(v3_setup):
    """When Analyst A is revoked by Owner A, all access to Project Alpha objects is immediately denied."""
    client = v3_setup['client']
    o_headers = {'Authorization': f"Bearer {v3_setup['tokens']['owner_a']}"}
    a_headers = {'Authorization': f"Bearer {v3_setup['tokens']['analyst_a']}", 'X-Project-Id': 'proj-alpha'}
    analyst_a_id = v3_setup['users']['analyst_a']['id']
    run_a_id = v3_setup['run_a']['id']
    def_a_id = v3_setup['def_a']['id']

    # Verify initial access
    assert client.get(f"/api/v1/definitions/{def_a_id}", headers=a_headers).status_code == 200
    assert client.get(f"/api/v1/comparisons/{run_a_id}", headers=a_headers).status_code == 200

    # Owner revokes Analyst A
    rev_res = client.post(f"/api/v1/projects/proj-alpha/members/{analyst_a_id}/revoke", headers=o_headers)
    assert rev_res.status_code == 200
    assert rev_res.json()['revoked'] is True

    # Immediately, Analyst A cannot access Project Alpha bootstrap
    assert client.get("/api/v1/bootstrap", headers=a_headers).status_code == 403

    # Direct access to known objects is denied
    assert client.get(f"/api/v1/definitions/{def_a_id}", headers=a_headers).status_code == 403
    assert client.get(f"/api/v1/comparisons/{run_a_id}", headers=a_headers).status_code == 403
    assert client.get(f"/api/v1/comparisons/{run_a_id}/evidence", headers=a_headers).status_code == 403
    assert client.get(f"/api/v1/comparisons/{run_a_id}/export?format=json", headers=a_headers).status_code == 403


def test_unverified_legacy_analyst_cannot_access_scoped_projects(v3_setup):
    """Unauthenticated calls fallback to unverified local analyst on local-retail only."""
    client = v3_setup['client']
    run_a_id = v3_setup['run_a']['id']

    # 1. Unauthenticated request to local-retail succeeds
    res = client.get("/api/v1/bootstrap")
    assert res.status_code == 200
    assert res.json()['project']['id'] == 'local-retail'

    # 2. Unauthenticated request with X-Project-Id: proj-alpha is denied
    res = client.get("/api/v1/bootstrap", headers={'X-Project-Id': 'proj-alpha'})
    assert res.status_code == 403

    # 3. Direct access to Project Alpha object without auth is denied
    res = client.get(f"/api/v1/comparisons/{run_a_id}")
    assert res.status_code == 403
