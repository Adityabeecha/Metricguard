"""Owner-only beta view: hidden from everyone else, and promoting a guest needs no redeploy."""
import pytest
from sqlalchemy import insert

from services.api.store import feedback, now
from tests.test_guest_accounts import sign_in
from tests.test_oauth import setup  # noqa: F401  (fixture)


@pytest.fixture
def modes(monkeypatch):
    monkeypatch.setenv('METRICGUARD_FULL_ACCESS_EMAILS', 'owner@example.com')
    monkeypatch.setenv('METRICGUARD_ADMIN_EMAILS', 'Owner@Example.com')


def test_only_listed_owners_see_the_admin_routes(setup, modes):
    client, store, _, profile = setup
    profile['email'] = 'stranger@example.com'
    sign_in(client, 'github')
    assert client.get('/api/v1/auth/identity').json()['is_admin'] is False
    for path in ('/api/v1/admin/overview', '/api/v1/admin/feedback', '/api/v1/admin/accounts'):
        assert client.get(path).status_code == 404
    client.cookies.clear()
    profile['email'] = 'owner@example.com'
    sign_in(client)
    assert client.get('/api/v1/auth/identity').json()['is_admin'] is True
    overview = client.get('/api/v1/admin/overview').json()
    assert overview['accounts'] == 2 and overview['guests'] == 1 and overview['full_accounts'] == 1
    assert len(overview['signups_by_day']) == 7 and sum(d['count'] for d in overview['signups_by_day']) == 2


def test_feedback_accounts_and_promote(setup, modes):
    client, store, _, profile = setup
    profile['email'] = 'stranger@example.com'
    sign_in(client, 'github')
    guest = store.get_user_by_session(client.cookies.get('mg_session'))
    with store.engine.begin() as conn:
        conn.execute(insert(feedback).values(id='f1', user_id=guest['id'], project_id=None, page='/w/x/cases',
                                             app_version=None, message='The upload stalls at 80%', created_at=now()))
    client.cookies.clear()
    profile['email'] = 'owner@example.com'
    sign_in(client)
    notes = client.get('/api/v1/admin/feedback').json()['items']
    assert notes[0]['message'] == 'The upload stalls at 80%' and notes[0]['email'] == 'stranger@example.com'
    listed = {a['email']: a for a in client.get('/api/v1/admin/accounts').json()['items']}
    assert listed['stranger@example.com']['guest_expires_at'] and listed['owner@example.com']['guest_expires_at'] is None
    path = f"/api/v1/admin/accounts/{guest['id']}/promote"
    assert client.post(path, headers={'x-metricguard-client': 'web'}).json()['guest_expires_at'] is None
    assert store.guest_expiry(guest['id']) is None
    assert client.post(path, headers={'x-metricguard-client': 'web'}).status_code == 409
