"""OAuth transport is entirely mocked; no Google/GitHub or account credentials."""
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from services.api.app import create_app
from services.api import oauth
from services.api.store import DomainError, now, records, snapshots, users


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'false')
    monkeypatch.setenv('METRICGUARD_APP_URL', 'https://metricguard.example')
    monkeypatch.setenv('METRICGUARD_SESSION_SECRET', 'test-only-secret-' * 4)
    monkeypatch.setenv('METRICGUARD_SIGNUP', 'invite')
    monkeypatch.setenv('METRICGUARD_INVITE_EMAILS', ' INVITED@example.com ')
    for provider in ('GOOGLE', 'GITHUB'):
        monkeypatch.setenv(provider + '_CLIENT_ID', 'test-id')
        monkeypatch.setenv(provider + '_CLIENT_SECRET', 'test-client-secret')
    app = create_app(database_url=f'sqlite:///{tmp_path / "app.db"}', data_dir=tmp_path)
    requests = []
    profile = {'email': 'invited@example.com', 'verified': True}

    def handle(request):
        requests.append(request)
        if request.url.path.endswith('/token') or request.url.path.endswith('/access_token'):
            return httpx.Response(200, json={'access_token': 'private-access-token', 'token_type': 'Bearer'})
        if request.url.path.endswith('/userinfo'):
            return httpx.Response(200, json={'sub': 'google-person', 'name': 'Beta Analyst',
                'email': profile['email'], 'email_verified': profile['verified']})
        if request.url.path == '/user':
            return httpx.Response(200, json={'id': 1234, 'login': 'beta', 'name': 'Beta Analyst'})
        if request.url.path == '/user/emails':
            return httpx.Response(200, json=[{'email': 'other@example.com', 'primary': False, 'verified': True},
                {'email': profile['email'], 'primary': True, 'verified': profile['verified']}])
        raise AssertionError(str(request.url))

    monkeypatch.setattr(oauth, 'http_client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    with TestClient(app, base_url='https://testserver', follow_redirects=False) as client:
        yield client, app.state.store, requests, profile


def begin(client, provider):
    response = client.get(f'/api/v1/auth/oauth/{provider}/start')
    assert response.status_code == 302
    state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    return response, state


def callback(client, provider, state, **extra):
    return client.get(f'/api/v1/auth/oauth/{provider}/callback', params={'state': state, 'code': 'private-code', **extra})


@pytest.mark.parametrize('provider', ['google', 'github'])
def test_happy_path_personal_workspace_and_existing_session(setup, provider):
    client, store, requests, _ = setup
    started, state = begin(client, provider)
    assert 'HttpOnly' in started.headers['set-cookie'] and 'SameSite=lax' in started.headers['set-cookie']
    response = callback(client, provider, state)
    assert response.status_code == 302
    location = response.headers['location']
    assert location.startswith('https://metricguard.example/w/') and location.endswith('/investigations')
    assert '@' not in location and 'private-' not in location
    assert 'mg_session=' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    user = store.get_user_by_session(client.cookies.get('mg_session'))
    assert user['email'] == 'invited@example.com'
    pid = location.split('/w/')[1].split('/')[0]
    assert store.get_project_member(pid, user['id'])['role'] == 'owner'
    if provider == 'google':
        auth = parse_qs(urlsplit(started.headers['location']).query)
        assert auth['code_challenge_method'] == ['S256']
        assert 'code_verifier' in parse_qs(requests[0].content.decode())
    _, again = begin(client, provider)
    repeated = callback(client, provider, again)
    assert repeated.headers['location'] == location
    assert len(store.list_projects_for_user(user['id'])) == 1


@pytest.mark.parametrize('state', [None, '', 'bad'])
def test_bad_or_missing_state_never_exchanges_or_creates(setup, state):
    client, store, requests, _ = setup
    begin(client, 'google')
    params = {'code': 'x', **({'state': state} if state is not None else {})}
    response = client.get('/api/v1/auth/oauth/google/callback', params=params)
    assert response.headers['location'].endswith('/signin?error=failed')
    assert not requests
    with store.engine.connect() as db:
        assert db.execute(select(users).where(users.c.id != 'local-analyst')).first() is None


@pytest.mark.parametrize('provider', ['google', 'github'])
def test_unverified_and_invite_gate_create_nothing(setup, provider):
    client, store, _, profile = setup
    profile['verified'] = False
    _, state = begin(client, provider)
    assert callback(client, provider, state).headers['location'].endswith('error=failed')
    profile.update(verified=True, email='not-invited@example.com')
    _, state = begin(client, provider)
    assert callback(client, provider, state).headers['location'].endswith('error=waitlist')
    with store.engine.connect() as db:
        assert db.execute(select(users).where(users.c.id != 'local-analyst')).first() is None


def test_existing_user_ignores_invite_gate_and_api_key_still_works(setup):
    client, store, _, profile = setup
    user, key = store.create_user('Existing analyst', 'existing@example.com')
    profile['email'] = 'EXISTING@example.com'
    _, state = begin(client, 'google')
    result = callback(client, 'google', state)
    assert '/w/' in result.headers['location']
    assert store.get_user_by_session(client.cookies['mg_session'])['id'] == user['id']
    result = client.post('/api/v1/auth/session', headers={'X-API-Key': key, 'x-metricguard-client': 'web'})
    assert result.status_code == 200 and result.json()['signed_in']


def test_providers_hide_incomplete_configuration(setup, monkeypatch):
    client, _, _, _ = setup
    assert client.get('/api/v1/auth/providers').json()['providers'] == ['google', 'github']
    monkeypatch.delenv('GOOGLE_CLIENT_SECRET')
    assert client.get('/api/v1/auth/providers').json()['providers'] == ['github']
    monkeypatch.delenv('METRICGUARD_SESSION_SECRET')
    assert client.get('/api/v1/auth/providers').json()['providers'] == []


def test_state_single_use_provider_binding_tamper_and_cancel(setup):
    client, _, requests, _ = setup
    _, state = begin(client, 'google')
    assert callback(client, 'github', state).headers['location'].endswith('error=failed')
    good = callback(client, 'google', state)
    assert '/w/' in good.headers['location']
    before = len(requests)
    assert callback(client, 'google', state).headers['location'].endswith('error=failed')
    assert len(requests) == before
    _, state = begin(client, 'google')
    assert callback(client, 'google', state, error='access_denied').headers['location'].endswith('error=cancelled')


def test_delete_requires_session_and_client_header(setup):
    client, store, _, _ = setup
    user, key = store.create_user('Key only', 'key@example.com')
    assert client.delete('/api/v1/auth/account', headers={'X-API-Key': key, 'x-metricguard-client': 'web'}).status_code == 401
    client.cookies.set('mg_session', store.create_session(user['id'], 3600), path='/api')
    assert client.delete('/api/v1/auth/account').status_code == 403
    result = client.delete('/api/v1/auth/account', headers={'x-metricguard-client': 'web'})
    assert result.status_code == 200 and result.json()['deleted']
    assert store.get_user_by_api_key(key) is None


def test_logout_requires_client_header_for_active_session(setup):
    client, store, _, _ = setup
    user, _ = store.create_user('Logout', 'logout@example.com')
    token = store.create_session(user['id'], 3600)
    client.cookies.set('mg_session', token, path='/api')
    assert client.delete('/api/v1/auth/session').status_code == 403
    assert store.get_user_by_session(token)
    assert client.delete('/api/v1/auth/session', headers={'x-metricguard-client': 'web'}).status_code == 200
    assert store.get_user_by_session(token) is None


def test_tampered_cookie_and_expired_state_do_not_exchange(setup, monkeypatch):
    client, _, requests, _ = setup
    _, state = begin(client, 'google')
    cookie = client.cookies.get('mg_oauth_state_google')
    client.cookies.clear()
    client.cookies.set('mg_oauth_state_google', cookie + 'x', path='/api/v1/auth/oauth/google')
    assert callback(client, 'google', state).headers['location'].endswith('error=failed')
    assert not requests
    _, state = begin(client, 'google')
    moment = oauth.time.time()
    monkeypatch.setattr(oauth.time, 'time', lambda: moment + 601)
    assert callback(client, 'google', state).headers['location'].endswith('error=failed')
    assert not requests


def test_open_signup_and_second_provider_reuse_one_personal_workspace(setup, monkeypatch):
    client, store, _, profile = setup
    monkeypatch.setenv('METRICGUARD_SIGNUP', 'open')
    profile['email'] = 'new@example.com'
    _, state = begin(client, 'google')
    google = callback(client, 'google', state)
    _, state = begin(client, 'github')
    github = callback(client, 'github', state)
    assert google.headers['location'] == github.headers['location']
    user = store.get_user_by_session(client.cookies.get('mg_session'))
    assert len(store.list_projects_for_user(user['id'])) == 1


def test_safe_network_failure_and_unknown_provider(setup, monkeypatch):
    client, _, _, _ = setup
    def broken():
        raise RuntimeError('private token or email MUST NOT be reflected')
    monkeypatch.setattr(oauth, 'http_client', broken)
    _, state = begin(client, 'google')
    response = callback(client, 'google', state)
    assert response.headers['location'] == 'https://metricguard.example/signin?error=failed'
    assert client.get('/api/v1/auth/oauth/unknown/callback').headers['location'].endswith('error=failed')
    assert client.get('/api/v1/auth/oauth/unknown/start').headers['location'].endswith('error=failed')


def test_configured_secure_cookie_flags(tmp_path, monkeypatch):
    monkeypatch.setenv('METRICGUARD_SECURE_COOKIES', 'true')
    monkeypatch.setenv('METRICGUARD_APP_URL', 'https://metricguard.example')
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'id')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'secret')
    monkeypatch.setenv('METRICGUARD_SESSION_SECRET', 's' * 64)
    app = create_app(database_url=f'sqlite:///{tmp_path / "secure.db"}', data_dir=tmp_path)
    with TestClient(app, base_url='https://testserver', follow_redirects=False) as client:
        started, _ = begin(client, 'google')
        assert 'Secure' in started.headers['set-cookie']
        assert 'HttpOnly' in started.headers['set-cookie']
        assert 'SameSite=lax' in started.headers['set-cookie']


def test_delete_account_route_removes_private_rows_and_uses_durable_remove(setup, monkeypatch):
    from pathlib import Path
    from services import durable
    client, store, _, _ = setup
    user, _ = store.create_user('Deleting analyst', 'erase@example.com')
    project = store.create_project('Personal data', owner_id=user['id'])
    root = Path(store.engine.url.database).parent
    artifact = root / 'snapshots' / 'private.duckdb'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b'private dataset')
    snapshot = store.record_snapshot('Private', {'row_count': 1, 'database_hash': 'fake'}, artifact, project['id'])
    with store.engine.begin() as db:
        db.execute(insert(records).values(id='private-case', project_id=project['id'], kind='investigation',
                                         created_at=now(), data={'title': 'Private case'}))
    calls, original = [], durable.remove
    def tracked(path, **kwargs):
        calls.append(Path(path))
        original(path, **kwargs)
    monkeypatch.setattr(durable, 'remove', tracked)
    client.cookies.set('mg_session', store.create_session(user['id'], 3600), path='/api')
    response = client.delete('/api/v1/auth/account', headers={'x-metricguard-client': 'web'})
    assert response.status_code == 200
    assert artifact in calls and not artifact.exists()
    assert 'Max-Age=0' in response.headers['set-cookie']
    with pytest.raises(DomainError):
        store.get_project(project['id'])
    with store.engine.connect() as db:
        assert db.execute(select(snapshots).where(snapshots.c.id == snapshot['id'])).first() is None
        assert db.execute(select(records).where(records.c.id == 'private-case')).first() is None


def test_public_auth_pages_are_served_by_local_api(tmp_path, monkeypatch):
    import importlib
    app_module = importlib.import_module('services.api.app')
    dist = tmp_path / 'apps' / 'web' / 'dist'
    dist.mkdir(parents=True)
    (dist / 'index.html').write_text('<html>OAuth application shell</html>')
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    app = create_app(database_url=f'sqlite:///{tmp_path / "shell.db"}', data_dir=tmp_path / 'data')
    with TestClient(app) as client:
        for path in ('/signin', '/signup', '/signin?error=waitlist', '/privacy', '/terms'):
            response = client.get(path)
            assert response.status_code == 200
            assert 'OAuth application shell' in response.text
        missing = client.get('/not-a-page', headers={'accept': 'text/html'})
        assert missing.status_code == 404 and 'OAuth application shell' in missing.text
        assert client.get('/missing.js').status_code == 404
        assert client.head('/').status_code == 200
        assert client.head('/').content == b''
        assert client.get('/api/v1/does-not-exist', headers={'accept': 'text/html'}).status_code == 404
        assert 'OAuth application shell' not in client.get('/api/v1/does-not-exist').text


def test_identity_supplies_accessible_home_workspace_without_an_extra_public_api_call(setup):
    client, store, _, _ = setup
    user, key = store.create_user('Homepage analyst', 'homepage@example.test')
    team = store.create_project('Shared team', user['id'])
    response = client.get('/api/v1/auth/identity', headers={'x-api-key': key})
    assert response.status_code == 200
    assert response.json()['home_workspace_id'] == team['id']
    # A personal workspace takes precedence; revoked memberships never become
    # homepage destinations, even when the identity itself remains valid.
    personal = store.create_project('Personal', user['id'], project_id='personal-' + user['id'])
    assert client.get('/api/v1/auth/identity', headers={'x-api-key': key}).json()['home_workspace_id'] == personal['id']
    store.revoke_project_member(personal['id'], user['id'])
    assert client.get('/api/v1/auth/identity', headers={'x-api-key': key}).json()['home_workspace_id'] == team['id']
    store.revoke_project_member(team['id'], user['id'])
    assert client.get('/api/v1/auth/identity', headers={'x-api-key': key}).json()['home_workspace_id'] is None
