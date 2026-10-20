"""In-app feedback: signed-in only, bounded text, ten notes per person per day."""
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store, feedback


def test_feedback_is_stored_validated_and_rate_limited(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    user, key = store.create_user('Analyst', 'analyst@example.com')
    headers = {'X-API-Key': key}
    with TestClient(create_app(database_url=url, data_dir=tmp_path)) as client:
        assert client.post('/api/v1/feedback', json={'message': '   '}, headers=headers).status_code == 422
        assert client.post('/api/v1/feedback', json={'message': 'x' * 4001}, headers=headers).status_code == 422
        sent = client.post('/api/v1/feedback', json={'message': 'The breakdown tab is great', 'page': '/w/a/investigations',
                                                     'project_id': 'a', 'app_version': 'web'}, headers=headers)
        assert sent.status_code == 201 and sent.json()['received']
        for _ in range(9):
            assert client.post('/api/v1/feedback', json={'message': 'again'}, headers=headers).status_code == 201
        assert client.post('/api/v1/feedback', json={'message': 'one too many'}, headers=headers).status_code == 429
        assert client.post('/api/v1/feedback', json={'message': 'anonymous'}).status_code == 401
    with store.engine.connect() as conn:
        rows = conn.execute(feedback.select()).mappings().all()
    assert len(rows) == 10 and rows[0]['user_id'] == user['id'] and rows[0]['page'] == '/w/a/investigations'
