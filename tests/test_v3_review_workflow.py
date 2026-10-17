"""V3 Shared Review & Superseding Decisions Workflow Tests.

Verifies:
- Review requests with review rationale and proposed intentional variants.
- Effective time tracking and intentional variant declarations.
- Superseding decisions tied to immutable versions.
- New decisions NEVER rewrite historical comparison evidence or runs.
"""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from scripts.seed_demo import seed


@pytest.fixture
def review_env(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    store = app.state.store

    owner, owner_key = store.create_user('Owner One', 'owner1@metricguard.internal')
    analyst, analyst_key = store.create_user('Analyst One', 'analyst1@metricguard.internal')

    proj = store.create_project('Governance Project', owner_id=owner['id'], project_id='proj-gov')
    store.add_project_member('proj-gov', analyst['id'], 'analyst')

    # Create initial definition v1
    def1 = store.record_definition(
        name='Gross Revenue', source_label='Warehouse', owner_label='Billing',
        raw_sql='SELECT SUM(order_amount) FROM orders', time_column='order_date',
        project_id='proj-gov'
    )
    # Create definition v2 (intentional variant)
    def1_v2 = store.record_definition(
        name='Gross Revenue', source_label='Warehouse', owner_label='Billing',
        raw_sql='SELECT SUM(order_amount) FROM orders WHERE payment_status = \'captured\'',
        time_column='order_date', definition_id=def1['id'], project_id='proj-gov'
    )

    snap = store.bootstrap('local-retail')['snapshots'][0]
    snap_gov = store.record_snapshot(
        name='Golden Snapshot Gov', manifest=snap['manifest'],
        artifact_path=snap['artifact_path'], project_id='proj-gov'
    )

    context = {
        'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
        'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
        'scope': 'synthetic_retail_all_orders'
    }

    # Queue comparison run between v1 and v2
    run_req = {
        'baseline_version_id': def1_v2['versions'][0]['id'],
        'target_version_id': def1_v2['versions'][1]['id'],
        'snapshot_id': snap_gov['id'],
        'context': context,
        'relationship': 'intentional_variant'
    }
    run = store.queue(run_req, idempotency_key='gov-run-1', execution_mode='demo', project_id='proj-gov')
    mock_result = {
        'baseline_value': '104000000.00', 'target_value': '98000000.00',
        'delta_target_minus_baseline': '-6000000.00', 'explanation_status': 'complete_ordered_bridge',
        'residual': '0.00', 'evidence': [{'row_id': 'ord-101', 'delta': '-6000000.00'}],
        'provenance': {'baseline_source_hash': 'h1', 'target_source_hash': 'h2', 'context_fingerprint': 'c1',
                       'snapshot_manifest_hash': snap_gov['manifest_hash']}
    }
    run = store.complete_demo(run['id'], mock_result)

    with TestClient(app) as client:
        yield {
            'client': client,
            'store': store,
            'owner_token': owner_key,
            'analyst_token': analyst_key,
            'owner': owner,
            'analyst': analyst,
            'def': def1_v2,
            'snap': snap_gov,
            'run': run,
            'mock_result': mock_result,
        }


def test_review_request_and_owner_decision_lifecycle(review_env):
    """Analyst requests review for a variant; Owner records an approving decision."""
    client = review_env['client']
    a_headers = {'Authorization': f"Bearer {review_env['analyst_token']}", 'X-Project-Id': 'proj-gov'}
    o_headers = {'Authorization': f"Bearer {review_env['owner_token']}", 'X-Project-Id': 'proj-gov'}

    run = review_env['run']
    v2_id = review_env['def']['versions'][1]['id']
    v1_id = review_env['def']['versions'][0]['id']

    # 1. Analyst submits review request with rationale and proposed effective time
    req_body = {
        'definition_id': review_env['def']['id'],
        'baseline_version_id': v1_id,
        'target_version_id': v2_id,
        'run_id': run['id'],
        'rationale': 'Exclude uncaptured orders to align with FY26 GAAP accounting policy.',
        'is_intentional_variant': True,
        'variant_rationale': 'Captured-only variant for finance reconciliation',
        'effective_time': '2026-08-01T00:00:00Z'
    }
    create_req = client.post("/api/v1/projects/proj-gov/review-requests", headers=a_headers, json=req_body)
    assert create_req.status_code == 201
    review_req = create_req.json()
    assert review_req['status'] == 'pending'
    assert review_req['rationale'] == req_body['rationale']
    assert review_req['is_intentional_variant'] is True
    assert review_req['effective_time'] == '2026-08-01T00:00:00Z'

    # 2. Both analyst and owner can list review requests
    list_reqs = client.get("/api/v1/projects/proj-gov/review-requests", headers=a_headers)
    assert list_reqs.status_code == 200
    assert any(r['id'] == review_req['id'] for r in list_reqs.json())

    # 3. Owner records decision accepting variant
    dec_body = {
        'review_request_id': review_req['id'],
        'approved_version_id': v2_id,
        'decision': 'variant_accepted',
        'rationale': 'Approved per Finance Audit Committee meeting on 2026-09-20.'
    }
    create_dec = client.post("/api/v1/projects/proj-gov/decisions", headers=o_headers, json=dec_body)
    assert create_dec.status_code == 201
    decision = create_dec.json()
    assert decision['decision'] == 'variant_accepted'
    assert decision['approved_version_id'] == v2_id
    assert decision['project_id'] == 'proj-gov'

    # Review request status transitioned
    updated_req = client.get("/api/v1/projects/proj-gov/review-requests", headers=a_headers).json()
    req_status = next(r for r in updated_req if r['id'] == review_req['id'])
    assert req_status['status'] == 'variant_accepted'


def test_superseding_decisions_tied_to_immutable_versions(review_env):
    """Owner supersedes an earlier decision; historical comparison evidence remains untouched."""
    client = review_env['client']
    o_headers = {'Authorization': f"Bearer {review_env['owner_token']}", 'X-Project-Id': 'proj-gov'}

    v1_id = review_env['def']['versions'][0]['id']
    v2_id = review_env['def']['versions'][1]['id']
    run = review_env['run']

    # 1. Record Decision 1
    d1 = client.post("/api/v1/projects/proj-gov/decisions", headers=o_headers, json={
        'approved_version_id': v1_id,
        'decision': 'approved',
        'rationale': 'Initial baseline approval.'
    }).json()

    # 2. Record Decision 2 superseding Decision 1
    d2 = client.post("/api/v1/projects/proj-gov/decisions", headers=o_headers, json={
        'supersedes_version_id': v1_id,
        'approved_version_id': v2_id,
        'decision': 'superseded',
        'rationale': 'Superseded by v2 after policy update.'
    }).json()

    assert d2['decision'] == 'superseded'
    assert d2['approved_version_id'] == v2_id
    assert d2['supersedes_version_id'] == v1_id

    # 3. List decisions in project
    decisions = client.get("/api/v1/projects/proj-gov/decisions", headers=o_headers).json()
    assert len(decisions) >= 2
    assert any(d['id'] == d1['id'] for d in decisions)
    assert any(d['id'] == d2['id'] for d in decisions)

    # 4. Critical invariant: historical run outputs and evidence are NOT modified
    saved_run = client.get(f"/api/v1/comparisons/{run['id']}", headers=o_headers).json()
    assert saved_run['result']['delta_target_minus_baseline'] == review_env['mock_result']['delta_target_minus_baseline']
    assert saved_run['result']['residual'] == '0.00'
    assert saved_run['result']['evidence'] == review_env['mock_result']['evidence']
