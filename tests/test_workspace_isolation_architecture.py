"""Workspace Context Architecture & Isolation Tests.

Verifies:
1. Objects created in Workspace A remain isolated to Workspace A.
2. Workspace B cannot list, compare against, or access drafts/evidence from Workspace A.
3. Missing project context does not silently become local-retail for mutations.
4. Fallback object resolution infers project_id from authorized object context.
5. Replays and runs strictly belong to their originating workspace.
"""
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from scripts.seed_demo import seed


@pytest.fixture
def multi_workspace_setup(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    store = app.state.store

    # 1. Create Workspace A and Workspace B
    u_a, key_a = store.create_user('User A', 'user_a@test.internal')
    u_b, key_b = store.create_user('User B', 'user_b@test.internal')

    ws_a = store.create_project('Workspace Alpha', owner_id=u_a['id'], project_id='ws-alpha')
    ws_b = store.create_project('Workspace Beta', owner_id=u_b['id'], project_id='ws-beta')

    client = TestClient(app)
    return {
        'client': client,
        'store': store,
        'ws_a': ws_a,
        'ws_b': ws_b,
        'key_a': key_a,
        'key_b': key_b,
    }


def test_workspace_isolation_and_cross_access_rejection(multi_workspace_setup):
    client = multi_workspace_setup['client']
    store = multi_workspace_setup['store']
    key_a = multi_workspace_setup['key_a']
    key_b = multi_workspace_setup['key_b']

    headers_a = {'X-API-Key': key_a, 'X-Project-Id': 'ws-alpha'}
    headers_b = {'X-API-Key': key_b, 'X-Project-Id': 'ws-beta'}

    # 1. Create Contract & Snapshot in Workspace A
    contract_payload = {
        'version': 3,
        'mode': 'flat_snapshot',
        'name': 'Alpha Contract',
        'row_key': 'id',
        'measure': 'revenue',
        'precision': 10,
        'scale': 2,
        'categories': ['channel'],
        'measure_meaning': 'Sales',
        'unit_label': 'INR',
        'values_comparable': True,
        'scope': 'entire_supplied_snapshot',
        'null_policy': 'native_null',
    }
    contract_res = client.post(
        '/api/v1/flat/contracts',
        headers=headers_a,
        json=contract_payload
    )
    assert contract_res.status_code == 201
    contract_a = contract_res.json()
    assert contract_a['project_id'] == 'ws-alpha'

    # Create flat definitions in Workspace A
    def_res_1 = client.post(
        '/api/v1/flat/definitions',
        headers=headers_a,
        json={
            'name': 'Baseline Def',
            'contract_id': contract_a['id'],
            'raw_sql': "SELECT SUM(revenue) FROM snapshot WHERE channel = 'Online'",
        }
    )
    assert def_res_1.status_code == 201
    def_a_1 = def_res_1.json()

    def_res_2 = client.post(
        '/api/v1/flat/definitions',
        headers=headers_a,
        json={
            'name': 'Target Def',
            'contract_id': contract_a['id'],
            'raw_sql': "SELECT SUM(revenue) FROM snapshot WHERE channel = 'Retail'",
        }
    )
    assert def_res_2.status_code == 201
    def_a_2 = def_res_2.json()

    # 2. Workspace B cannot list Workspace A's definitions or contracts
    boot_b = client.get('/api/v1/bootstrap', headers=headers_b).json()
    b_def_ids = [d['id'] for d in boot_b['definitions']]
    assert def_a_1['id'] not in b_def_ids
    assert def_a_2['id'] not in b_def_ids
    assert contract_a['id'] not in [c['id'] for c in boot_b.get('flat_contracts', [])]

    # 3. Workspace B cannot create definitions referencing Workspace A's contract (403 Forbidden)
    cross_def = client.post(
        '/api/v1/flat/definitions',
        headers=headers_b,
        json={
            'name': 'Cross Def',
            'contract_id': contract_a['id'],
            'raw_sql': 'SELECT SUM(revenue) FROM snapshot',
        }
    )
    assert cross_def.status_code == 403

    # 4. Workspace B cannot access Workspace A's draft
    draft_res = client.post(
        '/api/v1/projects/ws-alpha/investigation-drafts',
        headers=headers_a,
        json={
            'title': 'Alpha Investigation Draft',
            'baseline_definition_id': def_a_1['id'],
            'target_definition_id': def_a_2['id'],
            'status': 'draft',
        }
    )
    assert draft_res.status_code == 201
    draft_a = draft_res.json()

    cross_draft = client.get(
        f"/api/v1/projects/ws-beta/investigation-drafts/{draft_a['id']}",
        headers=headers_b
    )
    assert cross_draft.status_code == 403

    # 5. Missing project context on scoped operations does not silently fall back to local-retail
    # When user is authenticated as User A but forgets X-Project-Id header on contract creation:
    missing_ctx = client.post(
        '/api/v1/flat/contracts',
        headers={'X-API-Key': key_a},
        json={
            'version': 3,
            'mode': 'flat_snapshot',
            'name': 'Unscoped Contract',
            'row_key': 'id',
            'measure': 'amount',
            'precision': 10,
            'scale': 2,
            'categories': [],
            'measure_meaning': 'Amount',
            'unit_label': 'USD',
            'values_comparable': True,
            'scope': 'entire_supplied_snapshot',
            'null_policy': 'native_null',
        }
    )
    assert missing_ctx.status_code == 400
    assert missing_ctx.json()['error']['code'] == 'PROJECT_REQUIRED'

    snap_sample = store.bootstrap('local-retail')['snapshots'][0]
    snap_alpha = store.record_snapshot(
        name='Alpha Snapshot',
        manifest=snap_sample['manifest'],
        artifact_path=snap_sample['artifact_path'],
        project_id='ws-alpha',
    )

    # 6. Workspace B cannot execute comparisons referencing Workspace A's definitions or snapshot
    comp_cross = client.post(
        '/api/v1/flat/comparisons',
        headers={**headers_b, 'Idempotency-Key': 'cross-comp-1'},
        json={
            'baseline_version_id': def_a_1['versions'][0]['id'],
            'target_version_id': def_a_2['versions'][0]['id'],
            'snapshot_id': snap_alpha['id'],
            'relationship': 'intentional_variant',
            'context': {
                'mode': 'flat_snapshot',
                'scope': 'entire_supplied_snapshot',
                'contract_hash': contract_a['contract_hash'],
                'unit_label': 'INR',
                'values_comparable': True,
            },
        }
    )
    assert comp_cross.status_code == 403

    # 7. Workspace B cannot access Workspace A's comparison run or evidence
    # Queue a legitimate run in Workspace A
    run_a = store.queue(
        {
            'baseline_version_id': def_a_1['versions'][0]['id'],
            'target_version_id': def_a_2['versions'][0]['id'],
            'snapshot_id': snap_alpha['id'],
            'relationship': 'candidate',
            'context': {},
        },
        idempotency_key='run-alpha-1',
        execution_mode='demo',
        project_id='ws-alpha',
    )
    res_run = client.get(f"/api/v1/comparisons/{run_a['id']}", headers=headers_b)
    assert res_run.status_code == 403

    res_evidence = client.get(f"/api/v1/comparisons/{run_a['id']}/evidence", headers=headers_b)
    assert res_evidence.status_code == 403


def test_fallback_object_infers_workspace_when_header_omitted(multi_workspace_setup):
    """When a client references a valid workspace object (e.g. contract or snapshot) without an explicit header,
    backend infers project_id from the object rather than failing or falling back to local-retail."""
    client = multi_workspace_setup['client']
    key_a = multi_workspace_setup['key_a']

    headers_a = {'X-API-Key': key_a, 'X-Project-Id': 'ws-alpha'}

    # 1. Create Contract in Workspace A
    contract_payload = {
        'version': 3,
        'mode': 'flat_snapshot',
        'name': 'Alpha Infer Contract',
        'row_key': 'id',
        'measure': 'revenue',
        'precision': 10,
        'scale': 2,
        'categories': ['channel'],
        'measure_meaning': 'Sales',
        'unit_label': 'INR',
        'values_comparable': True,
        'scope': 'entire_supplied_snapshot',
        'null_policy': 'native_null',
    }
    c_res = client.post('/api/v1/flat/contracts', headers=headers_a, json=contract_payload)
    assert c_res.status_code == 201
    contract = c_res.json()

    # 2. Post flat definition with NO X-Project-Id header, only API key
    # Backend must infer project ws-alpha from contract_id
    def_res = client.post(
        '/api/v1/flat/definitions',
        headers={'X-API-Key': key_a},
        json={
            'name': 'Inferred Project Def',
            'contract_id': contract['id'],
            'raw_sql': "SELECT SUM(revenue) FROM snapshot WHERE channel = 'Online'",
        }
    )
    assert def_res.status_code == 201
    definition = def_res.json()
    assert definition['project_id'] == 'ws-alpha'
