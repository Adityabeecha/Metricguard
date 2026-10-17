from fastapi.testclient import TestClient

from scripts.seed_demo import seed
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import run_job
from services.supervisor.worker import Supervisor


def test_http_queue_supervisor_runner_reconciles_golden_fixture(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    seed(url, tmp_path)
    app = create_app(database_url=url, data_dir=tmp_path, mode='isolated')
    with TestClient(app) as client:
        data = client.get('/api/v1/bootstrap').json()
        context = {'start': '2026-08-01', 'end_exclusive': '2026-09-01', 'timezone': 'Asia/Kolkata',
                   'currency': 'INR', 'unit': 'currency', 'grain': 'one_row_per_order_id',
                   'scope': 'synthetic_retail_all_orders'}
        body = {'baseline_version_id': data['definitions'][0]['versions'][0]['id'],
                'target_version_id': data['definitions'][2]['versions'][0]['id'],
                'snapshot_id': data['snapshots'][0]['id'], 'context': context,
                'relationship': 'expected_agreement'}
        queued = client.post('/api/v1/comparisons', json=body, headers={'Idempotency-Key': 'worker-path'}).json()
        assert queued['state'] == 'queued'

        class CompletedProcess:
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0

        class InProcessLauncher:
            def start(self, input_dir, output_dir):
                run_job(input_dir, output_dir)
                return CompletedProcess()

        worker = Supervisor(Store(url), InProcessLauncher(), tmp_path / 'jobs', 'test-worker')
        assert worker.run_once()
        finished = client.get(f"/api/v1/comparisons/{queued['id']}").json()
        assert finished['state'] == 'succeeded', finished['error']
        assert finished['result']['delta_target_minus_baseline'] == '-6000000.00'
        assert finished['result']['residual'] == '0.00'
