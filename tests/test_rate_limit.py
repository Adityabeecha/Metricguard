"""Per-address limits on sign-in, public case links and feedback."""
from services.api.rate_limit import Limiter


def test_limits_apply_per_address_and_window(monkeypatch):
    monkeypatch.delenv('METRICGUARD_RATE_LIMITS', raising=False)
    limiter = Limiter(rules=(('/api/v1/auth/oauth/', 2, 60),))
    assert limiter.check('/api/v1/auth/oauth/google/start', 'a', now=0) is None
    assert limiter.check('/api/v1/auth/oauth/github/start', 'a', now=1) is None
    assert limiter.check('/api/v1/auth/oauth/google/start', 'a', now=2) == 58
    assert limiter.check('/api/v1/auth/oauth/google/start', 'b', now=2) is None  # another address
    assert limiter.check('/api/v1/projects', 'a', now=2) is None  # unlisted paths are never limited
    assert limiter.check('/api/v1/auth/oauth/google/start', 'a', now=61) is None  # the window moved on
    monkeypatch.setenv('METRICGUARD_RATE_LIMITS', 'off')
    assert all(limiter.check('/api/v1/auth/oauth/x', 'a', now=62) is None for _ in range(5))


def test_the_api_answers_429_with_retry_after(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from services.api import rate_limit
    from services.api.app import create_app
    monkeypatch.setattr(rate_limit, 'RULES', (('/api/v1/shared-cases/', 1, 60),))
    monkeypatch.setattr(rate_limit.Limiter.__init__, '__defaults__', (rate_limit.RULES,))
    with TestClient(create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}", data_dir=tmp_path)) as client:
        assert client.get('/api/v1/shared-cases/' + 'x' * 43).status_code == 404
        limited = client.get('/api/v1/shared-cases/' + 'x' * 43)
        assert limited.status_code == 429 and limited.headers['retry-after'] and limited.json()['error']['code'] == 'TOO_MANY_REQUESTS'
