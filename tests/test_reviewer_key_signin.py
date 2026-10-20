"""Reviewer access: an issued key with its own sandbox workspace, and the off-by-default hosted key sign-in switch."""
import pytest
from fastapi.testclient import TestClient

from scripts import issue_api_key
from services.api.app import create_app
from services.api.store import Store


@pytest.mark.parametrize('value,expected', [(None, False), ('', False), ('nope', False), ('true', True), ('ON', True)])
def test_key_signin_is_off_unless_enabled(tmp_path, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('METRICGUARD_KEY_SIGNIN', raising=False)
    else:
        monkeypatch.setenv('METRICGUARD_KEY_SIGNIN', value)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}", data_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/v1/auth/providers').json()['key_signin'] is expected


def test_reviewer_key_gets_only_its_own_sandbox(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv('METRICGUARD_DATABASE_URL', url)
    store = Store(url)
    owner, _ = store.create_user('Owner', 'owner@example.com')
    private = store.create_project('Owner data', owner['id'], project_id='owner-data')['id']

    assert issue_api_key.main(['--name', 'Reviewer', '--email', 'reviewer@example.com', '--new-workspace', 'Reviewer sandbox']) == 0
    printed = capsys.readouterr().out
    key = printed.strip().splitlines()[-1]
    assert key and 'owner in' in printed

    app = create_app(database_url=url, data_dir=tmp_path, mode='isolated')
    with TestClient(app) as client:
        identity = client.get('/api/v1/auth/identity', headers={'X-API-Key': key}).json()
        assert identity['verified'] is True
        sandbox = identity['home_workspace_id']
        assert sandbox and sandbox != private
        assert client.get('/api/v1/auth/identity', params={'project_id': sandbox}, headers={'X-API-Key': key}).json()['role'] == 'owner'
        denied = client.get('/api/v1/auth/identity', params={'project_id': private}, headers={'X-API-Key': key})
        assert denied.status_code == 403

    # Revoking the key ends access.
    assert issue_api_key.main(['--deactivate', 'reviewer@example.com']) == 0
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        assert client.get('/api/v1/auth/identity', headers={'X-API-Key': key}).status_code == 401
