import csv
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.store import Store
from services.supervisor.worker import Supervisor


def test_dataset_preview_is_queued_and_bounded_before_mapping(tmp_path):
    class Completed:
        def poll(self): return 0
        def wait(self, timeout=None): return 0

    class PreviewLauncher:
        def start(self, input_dir, output_dir, command=None):
            from services.supervisor.runner import preview_job
            assert command == 'preview'
            preview_job(input_dir, output_dir)
            return Completed()

    source = tmp_path / 'source.csv'
    with source.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['sale_ref', 'gross_inr', 'unused'])
        for index in range(9):
            writer.writerow([f's-{index}', f'{index}.00', 'sample'])
    raw = source.read_bytes()
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        created = client.post('/api/v1/dataset-previews',
                              files={'file': ('sales.csv', raw)})
        assert created.status_code == 202, created.text
        pending = created.json()
        assert pending['state'] == 'queued'
        assert 'sample_rows' not in pending
        worker = Supervisor(Store(url), PreviewLauncher(), tmp_path / 'jobs',
                            'preview-worker', data_dir=tmp_path)
        assert worker.run_once()
        result = client.get(f"/api/v1/dataset-previews/{pending['id']}")
        assert result.status_code == 200, result.text
        preview = result.json()['preview']
        assert result.json()['state'] == 'previewed'
        assert preview['headers'] == ['sale_ref', 'gross_inr', 'unused']
        assert len(preview['sample_rows']) == 5
        assert preview['source_hash'] == hashlib.sha256(raw).hexdigest()
        assert not list((tmp_path / 'uploads').glob('*.csv'))
        assert client.get('/api/v1/bootstrap').json()['imports'] == []
