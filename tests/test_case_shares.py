"""Public case capabilities stay scoped, read-only, revocable and hash-only."""
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from services.api.app import create_app
from services.api.store import Store, records
from services.supervisor.worker import Supervisor
from tests.test_distinct_and_ratio import _InProcess
from tests.test_item_management import _setup


def test_public_case_share_is_allowlisted_and_revocable(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    worker = Supervisor(store, _InProcess(), tmp_path / 'jobs', 'share-worker')
    with TestClient(create_app(database_url=store, data_dir=tmp_path, mode='isolated')) as client:
        _, run = _setup(tmp_path, client, worker)
        case = client.post('/api/v1/investigations', json={'run_id': run['id'], 'title': 'Paid only',
                    'decision': 'Void order removed.', 'actor_label': 'private@example.com'}).json()
        base = f"/api/v1/investigations/{case['id']}/shares"
        created = client.post(base, json={'expires_in_days': 7})
        assert created.status_code == 201, created.text
        share = created.json()
        assert len(share['token']) >= 43
        token = share['token']
        public = f'/api/v1/shared-cases/{token}'
        with store.engine.connect() as conn:
            persisted = list(conn.execute(select(records.c.data).where(records.c.kind == 'case_share')).scalars())
        assert token not in json.dumps(persisted)
        assert token not in client.get(base).text
        monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'false')
        opened = client.get(public)
        assert opened.status_code == 200, opened.text
        dto = opened.json()
        assert dto['title'] == 'Paid only' and dto['run']['id'] == run['id']
        for private in ('actor_label', 'project_id', 'artifact_path', 'source_path', 'private@example.com'):
            assert private not in opened.text
        for suffix in ('evidence-summary', 'evidence?limit=1', 'evidence-breakdown?column=status',
                       'evidence-group?column=status&value=%22void%22'):
            response = client.get(f'{public}/{suffix}')
            assert response.status_code == 200, response.text
        assert client.get(f'{public}/evidence?run_id=other').status_code == 200
        assert client.get(f'{public}/bootstrap').status_code == 404
        assert client.post(f'{public}/replays').status_code in (404, 405)  # no replay route for shared links
        assert client.patch(public, json={'decision': 'changed'}).status_code == 405
        assert client.get(f"/api/v1/comparisons/{run['id']}").status_code == 401
        monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true')
        assert client.delete(f"{base}/{share['id']}").status_code == 200
        assert client.get(public).status_code == 404
        assert client.get(f'{public}/evidence-summary').status_code == 404


def test_share_roles_expiry_case_binding_and_invalid_tokens(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'app.db'}")
    worker = Supervisor(store, _InProcess(), tmp_path / 'jobs', 'share-worker')
    with TestClient(create_app(database_url=store, data_dir=tmp_path, mode='isolated')) as client:
        _, run = _setup(tmp_path, client, worker)
        case = client.post('/api/v1/investigations', json={'run_id': run['id'], 'title': 'First',
                       'decision': 'Reason', 'actor_label': 'Owner'}).json()
        other = client.post('/api/v1/investigations', json={'run_id': run['id'], 'title': 'Other',
                       'decision': 'Other reason', 'actor_label': 'Owner'}).json()
        base = f"/api/v1/investigations/{case['id']}/shares"
        reader, key = store.create_user('Reader', 'reader@example.com')
        store.add_project_member(store.project_id, reader['id'], 'reader')
        for method in (client.get, client.post):
            assert method(base, headers={'X-API-Key': key}).status_code == 403
        analyst, analyst_key = store.create_user('Analyst', 'analyst@example.com')
        store.add_project_member(store.project_id, analyst['id'], 'analyst')
        _, outsider_key = store.create_user('Outsider', 'outsider@example.com')
        assert client.get(base, headers={'X-API-Key': outsider_key}).status_code == 403
        assert client.post(base, json={}, headers={'X-API-Key': outsider_key}).status_code == 403
        assert client.post(base, json={}, headers={'X-Project-Id': 'other-workspace'}).status_code == 403
        share = client.post(base, json={'expires_in_days': None}, headers={'X-API-Key': analyst_key}).json()
        assert share['expires_at'] is None
        assert client.delete(f"/api/v1/investigations/{other['id']}/shares/{share['id']}").status_code == 404
        assert client.delete(f"{base}/{share['id']}", headers={'X-API-Key': key}).status_code == 403
        assert client.post(base, json={'expires_in_days': 2}).status_code == 422
        thirty = client.post(base, json={'expires_in_days': 30})
        assert thirty.status_code == 201
        thirty_token = thirty.json()['token']
        assert client.get(f'/api/v1/shared-cases/{thirty_token}').status_code == 200
        with store.engine.begin() as conn:
            row = conn.execute(select(records).where(records.c.kind == 'case_share',
                    records.c.data['share_id'].as_string() == share['id'])).mappings().one()
            conn.execute(update(records).where(records.c.id == row['id']).values(data={**row['data'],
                         'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}))
        assert client.get(f"/api/v1/shared-cases/{share['token']}").status_code == 404
        assert client.get('/api/v1/shared-cases/invalid').status_code == 404
        assert client.delete(f"/api/v1/investigations/{case['id']}").status_code == 200
        assert client.get(f'/api/v1/shared-cases/{thirty_token}').status_code == 404


def test_public_result_dto_drops_private_and_unknown_metadata():
    from services.api.case_shares import public_result, read_dto
    secret = '/private/actor@example.com/other-case.jsonl'
    source = {'baseline_value': '1', 'target_value': '2', 'actor_label': secret,
              'artifact_path': secret, 'provenance': {'database_hash': 'a' * 64, 'source_path': secret, 'actor_label': secret},
              'evidence_coverage': {'mode': 'changed_records', 'total_records': 2, 'artifact_path': secret},
              'evidence_artifact': {'sha256': 'b' * 64, 'row_count': 2, 'path': secret},
              'evidence': [{'order_id': 'R1', 'delta': '1', 'actor_label': secret, 'path': secret}]}
    assert secret not in json.dumps(public_result(source))
    assert secret not in json.dumps(read_dto('evidence', {'items': source['evidence'], 'total': 1, 'project_id': secret}))
    assert public_result(source)['evidence_artifact']['sha256'] == 'b' * 64
