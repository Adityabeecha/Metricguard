"""Beta waitlist: request access stores an email once, is rate-limited, and never grants sign-in."""
import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import DomainError, Store


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('METRICGUARD_SIGNUP', 'invite')
    monkeypatch.delenv('METRICGUARD_INVITE_EMAILS', raising=False)
    url = f"sqlite:///{tmp_path / 'app.db'}"
    app = create_app(database_url=url, data_dir=tmp_path)
    return TestClient(app), Store(url)


def test_request_access_stores_the_email_once(setup):
    client, store = setup
    with client:
        for email in ('Ana@Example.com', ' ana@example.com '):
            response = client.post('/api/v1/auth/access-requests', json={'email': email}, headers={'x-forwarded-for': '1.1.1.1'})
            assert response.status_code == 202, response.text
            assert response.json() == {'status': 'received'}
    rows = store.access_request_list()
    assert [r['email'] for r in rows] == ['ana@example.com']
    assert rows[0]['request_count'] == 2


@pytest.mark.parametrize('body', [{'email': 'not-an-email'}, {'email': 'a@b'}, {'email': 'x' * 250 + '@example.com'},
                                  {'email': 5}, {}, ['a@example.com']])
def test_request_access_rejects_invalid_input(setup, body):
    client, store = setup
    with client:
        response = client.post('/api/v1/auth/access-requests', json=body)
    assert response.status_code == 422
    assert store.access_request_list() == []


def test_request_access_is_rate_limited_per_visitor(setup):
    client, store = setup
    with client:
        codes = [client.post('/api/v1/auth/access-requests', json={'email': f'p{i}@example.com'},
                             headers={'x-forwarded-for': '2.2.2.2'}).status_code for i in range(6)]
        other = client.post('/api/v1/auth/access-requests', json={'email': 'q@example.com'}, headers={'x-forwarded-for': '3.3.3.3'})
    assert codes == [202] * 5 + [429]
    assert other.status_code == 202
    assert len(store.access_request_list()) == 6


def test_waitlist_is_capped(setup):
    _, store = setup
    store.request_access('one@example.com', max_rows=1)
    store.request_access('one@example.com', max_rows=1)  # a repeat still counts, not refused
    with pytest.raises(DomainError) as refused:
        store.request_access('two@example.com', max_rows=1)
    assert refused.value.code == 'ACCESS_REQUESTS_FULL'


def test_requesting_access_does_not_allow_sign_in(setup):
    _, store = setup
    store.request_access('wait@example.com')
    with pytest.raises(DomainError) as refused:
        store.oauth_sign_in('google', 'subject-1', 'wait@example.com', 'Wait', 'invite', set())
    assert refused.value.code == 'OAUTH_WAITLIST'
