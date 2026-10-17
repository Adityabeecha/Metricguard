"""Comprehensive test suite for Milestone V5.2-05:
Durable Server-Backed Investigation Drafts + Contextual Decisions + Workspace Isolation.

Covers:
1. Draft Lifecycle (CRUD):
   - Create investigation draft
   - Retrieve investigation draft
   - List investigation drafts
   - Update investigation draft
   - Delete investigation draft
2. Optimistic Concurrency & Conflict Detection:
   - Successful update rotates version_token
   - Stale version_token returns 409 CONFLICT
3. Workspace Isolation:
   - Draft created in workspace A is invisible to workspace B
   - Cross-workspace update/delete rejected with 404
4. Unified Resolution:
   - GET /api/v1/investigations/{draft_id} resolves draft transparently
5. Bootstrap Integration:
   - investigation_drafts included in project bootstrap payload
6. RBAC Enforcement:
   - Reader cannot mutate drafts; Analyst/Owner can
"""
from __future__ import annotations

from pathlib import Path
import pytest
from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store


@pytest.fixture
def test_setup(tmp_path: Path):
    db_path = tmp_path / 'metricguard.db'
    store = Store(f'sqlite:///{db_path}')
    app = create_app(data_dir=tmp_path, database_url=store)
    client = TestClient(app)

    pid = 'local-retail'
    # Ensure default workspace exists
    try:
        store.create_project(name='Local Retail', project_id=pid)
    except Exception:
        pass

    # Setup roles
    u_analyst, key_analyst = store.create_user('Analyst User', 'analyst@corp.com')
    store.add_project_member(pid, u_analyst['id'], 'analyst')

    u_reader, key_reader = store.create_user('Reader User', 'reader@corp.com')
    store.add_project_member(pid, u_reader['id'], 'reader')

    return {
        'store': store,
        'client': client,
        'pid': pid,
        'key_analyst': key_analyst,
        'key_reader': key_reader,
        'user_analyst': u_analyst,
        'user_reader': u_reader,
    }


def test_investigation_draft_crud_lifecycle(test_setup):
    """Test standard create, get, list, update, and delete lifecycle for investigation drafts."""
    client = test_setup['client']
    pid = test_setup['pid']
    headers = {'X-API-Key': test_setup['key_analyst']}

    # 1. Create Draft
    create_payload = {
        'title': 'Investigate Revenue Gap',
        'baseline_definition_id': 'def-base-1',
        'baseline_version_id': 'ver-base-1',
        'target_definition_id': 'def-target-2',
        'target_version_id': 'ver-target-2',
        'snapshot_id': 'snap-2026-08',
        'status': 'draft',
        'notes': 'Checking unexpected drop in South region revenue.',
    }
    res = client.post(f'/api/v1/projects/{pid}/investigation-drafts', json=create_payload, headers=headers)
    assert res.status_code in (200, 201), res.text
    draft = res.json()
    draft_id = draft['id']
    assert draft['title'] == 'Investigate Revenue Gap'
    assert draft['status'] == 'draft'
    assert draft['project_id'] == pid
    assert draft['version_token'] is not None
    initial_token = draft['version_token']

    # 2. Get Draft
    res_get = client.get(f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}', headers=headers)
    assert res_get.status_code == 200
    got = res_get.json()
    assert got['id'] == draft_id
    assert got['notes'] == 'Checking unexpected drop in South region revenue.'

    # 3. List Drafts
    res_list = client.get(f'/api/v1/projects/{pid}/investigation-drafts', headers=headers)
    assert res_list.status_code == 200
    items = res_list.json()['items']
    assert any(d['id'] == draft_id for d in items)

    # 4. Update Draft
    update_payload = {
        'title': 'Investigate Revenue Gap - Confirmed Filter Drift',
        'notes': 'Root cause identified: target query introduces region = South filter constraint.',
        'version_token': initial_token,
    }
    res_up = client.put(f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}', json=update_payload, headers=headers)
    assert res_up.status_code == 200
    updated = res_up.json()
    assert updated['title'] == 'Investigate Revenue Gap - Confirmed Filter Drift'
    assert updated['version_token'] != initial_token

    # 5. Delete Draft
    res_del = client.delete(f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}', headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()['deleted'] is True

    # Confirm not found after deletion
    res_after = client.get(f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}', headers=headers)
    assert res_after.status_code == 404


def test_investigation_draft_optimistic_concurrency_409(test_setup):
    """Test that updating a draft with a stale version_token returns 409 Conflict."""
    client = test_setup['client']
    pid = test_setup['pid']
    headers = {'X-API-Key': test_setup['key_analyst']}

    res = client.post(f'/api/v1/projects/{pid}/investigation-drafts', json={'title': 'Concurrent Draft'}, headers=headers)
    assert res.status_code in (200, 201)
    draft = res.json()
    draft_id = draft['id']
    token_v1 = draft['version_token']

    # Update 1 succeeds and rotates token
    res_up1 = client.put(
        f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}',
        json={'title': 'First Edit', 'version_token': token_v1},
        headers=headers,
    )
    assert res_up1.status_code == 200
    token_v2 = res_up1.json()['version_token']
    assert token_v2 != token_v1

    # Update 2 with stale token_v1 must fail with 409
    res_stale = client.put(
        f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}',
        json={'title': 'Stale Edit', 'version_token': token_v1},
        headers=headers,
    )
    assert res_stale.status_code == 409
    err = res_stale.json()
    assert err['error']['code'] in ('STALE_DRAFT', 'DRAFT_CONFLICT')


def test_investigation_draft_workspace_isolation(test_setup):
    """Test that drafts in workspace A are strictly invisible to workspace B."""
    store = test_setup['store']
    client = test_setup['client']

    pid_a = f'workspace-a-{uuid4().hex[:6]}'
    pid_b = f'workspace-b-{uuid4().hex[:6]}'
    store.create_project(name='Alpha', project_id=pid_a)
    store.create_project(name='Beta', project_id=pid_b)

    u_a, key_a = store.create_user('User A', 'user_a@corp.com')
    store.add_project_member(pid_a, u_a['id'], 'analyst')

    u_b, key_b = store.create_user('User B', 'user_b@corp.com')
    store.add_project_member(pid_b, u_b['id'], 'analyst')

    # Create draft in Alpha
    res_a = client.post(
        f'/api/v1/projects/{pid_a}/investigation-drafts',
        json={'title': 'Alpha Only Draft'},
        headers={'X-API-Key': key_a},
    )
    assert res_a.status_code in (200, 201)
    draft_a = res_a.json()
    draft_a_id = draft_a['id']

    # Query Beta workspace list: must NOT contain draft_a
    res_b_list = client.get(f'/api/v1/projects/{pid_b}/investigation-drafts', headers={'X-API-Key': key_b})
    assert res_b_list.status_code == 200
    items_b = res_b_list.json()['items']
    assert not any(d['id'] == draft_a_id for d in items_b)

    # Direct query Beta for draft_a: cross-project access rejected
    res_b_get = client.get(f'/api/v1/projects/{pid_b}/investigation-drafts/{draft_a_id}', headers={'X-API-Key': key_b})
    assert res_b_get.status_code in (403, 404)

    # Delete from Beta: cross-project access rejected
    res_b_del = client.delete(f'/api/v1/projects/{pid_b}/investigation-drafts/{draft_a_id}', headers={'X-API-Key': key_b})
    assert res_b_del.status_code in (403, 404)


def test_investigation_draft_resolved_via_investigations_endpoint(test_setup):
    """Test that GET /api/v1/investigations/{ident} resolves draft transparently."""
    client = test_setup['client']
    pid = test_setup['pid']
    headers = {'X-API-Key': test_setup['key_analyst']}

    res = client.post(
        f'/api/v1/projects/{pid}/investigation-drafts',
        json={'title': 'Transparent Draft Resolution'},
        headers=headers,
    )
    assert res.status_code in (200, 201)
    draft_id = res.json()['id']

    res_inv = client.get(f'/api/v1/investigations/{draft_id}', headers=headers)
    assert res_inv.status_code == 200
    inv = res_inv.json()
    assert inv['id'] == draft_id
    assert inv['title'] == 'Transparent Draft Resolution'


def test_investigation_draft_in_bootstrap(test_setup):
    """Test that investigation drafts are returned in the workspace bootstrap payload."""
    client = test_setup['client']
    pid = test_setup['pid']
    headers = {'X-API-Key': test_setup['key_analyst']}

    res_create = client.post(
        f'/api/v1/projects/{pid}/investigation-drafts',
        json={'title': 'Bootstrap Draft'},
        headers=headers,
    )
    assert res_create.status_code in (200, 201)
    draft_id = res_create.json()['id']

    res_boot = client.get(f'/api/v1/bootstrap?project_id={pid}', headers=headers)
    assert res_boot.status_code == 200
    boot = res_boot.json()
    assert 'investigation_drafts' in boot
    assert any(d['id'] == draft_id for d in boot['investigation_drafts'])


def test_investigation_draft_rbac_reader_forbidden(test_setup):
    """Test that reader role cannot create, update, or delete drafts."""
    client = test_setup['client']
    pid = test_setup['pid']
    headers_reader = {'X-API-Key': test_setup['key_reader']}
    headers_analyst = {'X-API-Key': test_setup['key_analyst']}

    # Reader cannot create
    res_create = client.post(
        f'/api/v1/projects/{pid}/investigation-drafts',
        json={'title': 'Reader Attempt'},
        headers=headers_reader,
    )
    assert res_create.status_code == 403

    # Analyst creates draft
    res_analyst = client.post(
        f'/api/v1/projects/{pid}/investigation-drafts',
        json={'title': 'Analyst Created'},
        headers=headers_analyst,
    )
    assert res_analyst.status_code in (200, 201)
    draft_id = res_analyst.json()['id']
    token = res_analyst.json()['version_token']

    # Reader cannot update
    res_update = client.put(
        f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}',
        json={'title': 'Reader Edit', 'version_token': token},
        headers=headers_reader,
    )
    assert res_update.status_code == 403

    # Reader cannot delete
    res_del = client.delete(
        f'/api/v1/projects/{pid}/investigation-drafts/{draft_id}',
        headers=headers_reader,
    )
    assert res_del.status_code == 403
