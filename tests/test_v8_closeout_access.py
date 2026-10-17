"""V8 closeout: the workspace chooser lists only workspaces the caller can open."""
from fastapi.testclient import TestClient

from scripts.issue_api_key import main as issue_key
from services.api.app import create_app


def _client(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    app = create_app(database_url=url, data_dir=tmp_path, mode='demo')
    return app, TestClient(app)


def test_verified_key_lists_only_active_memberships_with_role(tmp_path):
    app, client = _client(tmp_path)
    store = app.state.store
    store.create_project('Finance', project_id='finance')
    store.create_project('Secret', project_id='secret')
    user, key = store.create_user('Asha', 'asha@example.com')
    store.add_project_member('finance', user['id'], 'analyst')
    store.add_project_member('secret', user['id'], 'reader')
    store.revoke_project_member('secret', user['id'])

    listed = client.get('/api/v1/projects', headers={'X-API-Key': key}).json()
    assert listed == [{'id': 'finance', 'name': 'Finance', 'role': 'analyst'}]
    # Authorization itself is unchanged: the revoked workspace still refuses.
    assert client.get('/api/v1/bootstrap?project_id=secret', headers={'X-API-Key': key}).status_code == 403


def test_unverified_local_lists_default_plus_local_memberships_only(tmp_path, monkeypatch):
    monkeypatch.delenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', raising=False)
    app, client = _client(tmp_path)
    store = app.state.store
    store.create_project('Other team', project_id='other-team')
    store.create_project('Local extra', owner_id='local-analyst', project_id='local-extra')

    ids = [p['id'] for p in client.get('/api/v1/projects').json()]
    assert ids[0] == store.project_id
    assert 'local-extra' in ids
    assert 'other-team' not in ids


def test_invalid_key_is_401_and_unverified_disabled_is_401(tmp_path, monkeypatch):
    _, client = _client(tmp_path)
    assert client.get('/api/v1/projects', headers={'X-API-Key': 'mg_wrong'}).status_code == 401
    monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'false')
    assert client.get('/api/v1/bootstrap').status_code == 401


def test_issue_api_key_command_grants_roles(tmp_path, monkeypatch, capsys):
    app, client = _client(tmp_path)
    app.state.store.create_project('Demo', project_id='demo')
    monkeypatch.setenv('METRICGUARD_DATABASE_URL', f"sqlite:///{tmp_path / 'app.db'}")
    assert issue_key(['--name', 'Ravi', '--email', 'ravi@example.com', '--workspace', 'demo:reader']) == 0
    key = capsys.readouterr().out.strip().splitlines()[-1]
    assert key.startswith('mg_')
    assert client.get('/api/v1/projects', headers={'X-API-Key': key}).json() == [{'id': 'demo', 'name': 'Demo', 'role': 'reader'}]
    assert issue_key(['--name', 'X', '--email', 'x@example.com', '--workspace', 'missing:reader']) == 2
