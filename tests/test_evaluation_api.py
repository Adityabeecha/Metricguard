import json

from fastapi.testclient import TestClient

from services.api.app import create_app


def test_evaluation_endpoint_missing_and_bounded_report(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}", data_dir=tmp_path, mode='demo')
    with TestClient(app) as client:
        missing = client.get('/api/v1/evaluation')
        assert missing.status_code == 200
        assert missing.json() == {'status': 'not_run', 'message': 'No local evaluation report is available.'}
        report_dir = tmp_path / 'evaluation'
        report_dir.mkdir()
        expected = {'status': 'passed', 'rows': 100000, 'oracle_match': {'dashboard_a': True}}
        (report_dir / 'release_report.json').write_text(json.dumps(expected))
        response = client.get('/api/v1/evaluation')
        assert response.status_code == 200
        assert response.json() == expected
        candidate = client.get('/api/v1/evaluation/candidates')
        assert candidate.json()['status'] == 'not_run'
        candidate_report = {'pair_count': 132, 'splits': {'holdout': {'pair_precision': 0.5}}}
        (report_dir / 'candidate_report.json').write_text(json.dumps(candidate_report))
        assert client.get('/api/v1/evaluation/candidates').json() == candidate_report
