"""Open sign-up guests: erased after their lifetime, 25 MB storage; listed emails keep full access."""
import time

import pytest
from sqlalchemy import update

from services.api import guests
from services.api.store import DomainError, guest_accounts
from tests.test_oauth import begin, callback, setup  # noqa: F401  (fixture)


def sign_in(client, provider='google'):
    _, state = begin(client, provider)
    response = callback(client, provider, state)
    assert response.status_code == 302 and '/w/' in response.headers['location'], response.headers['location']
    return response.headers['location'].split('/w/')[1].split('/')[0]


@pytest.fixture
def guest_mode(monkeypatch):
    monkeypatch.setenv('METRICGUARD_FULL_ACCESS_EMAILS', 'owner@example.com, Friend@Example.com')


def test_unlisted_email_becomes_a_guest_with_quota_and_notice(setup, guest_mode, tmp_path):
    client, store, _, profile = setup
    profile['email'] = 'stranger@example.com'
    pid = sign_in(client)
    user = store.get_user_by_session(client.cookies.get('mg_session'))
    expires = store.guest_expiry(user['id'])
    assert expires and abs(expires - (time.time() + 8 * 3600)) < 60
    assert store.get_project_quota(pid)['max_storage_bytes'] == 25 * 1024 * 1024
    identity = client.get('/api/v1/auth/identity').json()
    assert identity['guest_expires_at'] == pytest.approx(expires)
    with pytest.raises(DomainError) as refused:
        store.check_storage_quota(pid, 26 * 1024 * 1024, tmp_path)
    assert refused.value.code == 'QUOTA_EXCEEDED' and 'Guest accounts can store up to 25.0 MB' in refused.value.message
    store.check_storage_quota(pid, 5 * 1024 * 1024, tmp_path)


@pytest.mark.parametrize('email', ['owner@example.com', 'FRIEND@example.com'])
def test_listed_email_gets_full_access(setup, guest_mode, email):
    client, store, _, profile = setup
    profile['email'] = email
    pid = sign_in(client)
    user = store.get_user_by_session(client.cookies.get('mg_session'))
    assert store.guest_expiry(user['id']) is None and store.get_project_quota(pid) is None
    assert client.get('/api/v1/auth/identity').json()['guest_expires_at'] is None


def test_no_list_means_no_guests(setup):
    client, store, _, profile = setup
    profile['email'] = 'anyone@example.com'
    sign_in(client)
    user = store.get_user_by_session(client.cookies.get('mg_session'))
    assert store.guest_expiry(user['id']) is None


def test_existing_accounts_are_never_made_guests(setup, monkeypatch):
    client, store, _, profile = setup
    profile['email'] = 'early@example.com'
    sign_in(client)
    monkeypatch.setenv('METRICGUARD_FULL_ACCESS_EMAILS', 'owner@example.com')
    client.cookies.clear()
    sign_in(client)
    user = store.get_user_by_session(client.cookies.get('mg_session'))
    assert store.guest_expiry(user['id']) is None


def test_guest_added_to_list_is_promoted(setup, guest_mode, monkeypatch):
    client, store, _, profile = setup
    profile['email'] = 'later@example.com'
    pid = sign_in(client)
    monkeypatch.setenv('METRICGUARD_FULL_ACCESS_EMAILS', 'owner@example.com,later@example.com')
    client.cookies.clear()
    sign_in(client)
    user = store.get_user_by_session(client.cookies.get('mg_session'))
    assert store.guest_expiry(user['id']) is None and store.get_project_quota(pid) is None


def test_sweep_erases_only_expired_guests(setup, guest_mode, tmp_path):
    client, store, _, profile = setup
    profile['email'] = 'owner@example.com'
    owner_pid = sign_in(client)
    client.cookies.clear()
    profile['email'] = 'stranger@example.com'
    guest_pid = sign_in(client, 'github')  # the mock gives each provider one fixed subject
    guest = store.get_user_by_session(client.cookies.get('mg_session'))
    assert guests.sweep(store, tmp_path) == []  # not expired yet
    with store.engine.begin() as conn:
        conn.execute(update(guest_accounts).values(expires_at=time.time() - 1))
    assert guests.sweep(store, tmp_path) == [guest['id']]
    with pytest.raises(DomainError):
        store.get_user(guest['id'])
    with pytest.raises(DomainError):
        store.get_project(guest_pid)
    assert store.get_project(owner_pid)
    assert store.get_user_by_session(client.cookies.get('mg_session')) is None
    # Signing in again starts a fresh guest account.
    sign_in(client, 'github')
    fresh = store.get_user_by_session(client.cookies.get('mg_session'))
    assert fresh['id'] != guest['id'] and store.guest_expiry(fresh['id'])


def test_guest_terms_settings(monkeypatch):
    monkeypatch.delenv('METRICGUARD_FULL_ACCESS_EMAILS', raising=False)
    monkeypatch.setenv('METRICGUARD_INVITE_EMAILS', 'a@example.com')
    monkeypatch.setenv('METRICGUARD_GUEST_HOURS', '10')
    monkeypatch.setenv('METRICGUARD_GUEST_STORAGE_MB', 'nonsense')
    assert guests.guest_terms('a@example.com') is None
    assert guests.guest_terms('b@example.com') == {'seconds': 36000, 'storage_bytes': 25 * 1024 * 1024}
    monkeypatch.setenv('METRICGUARD_INVITE_EMAILS', '')
    assert guests.guest_terms('b@example.com') is None
