from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from packages.metricguard_core import ingest_csv
from packages.metricguard_core.common import SCHEMA
from services.api.app import create_app
from services.api.store import Store
from services.supervisor.runner import ingest_job, preview_job
from services.supervisor.worker import Supervisor
from tests.test_v2_contract import CONTRACT


FIXTURE = Path(__file__).resolve().parents[1] / 'MetricGuard_Planning_Pack/examples/orders_golden.csv'


def test_parquet_preview_then_approved_mapping_import(tmp_path):
    class Completed:
        def poll(self): return 0
        def wait(self, timeout=None): return 0

    class Launcher:
        def start(self, input_dir, output_dir, command=None):
            (preview_job if command == 'preview' else ingest_job)(input_dir, output_dir)
            return Completed()

    canonical = tmp_path / 'canonical.duckdb'
    ingest_csv(FIXTURE, canonical)
    source = tmp_path / 'renamed.parquet'
    with duckdb.connect(str(canonical)) as con:
        columns = ', '.join(f'"{role}" AS "source_{role}"' for role in SCHEMA)
        con.execute(f'COPY (SELECT {columns} FROM orders) TO ? (FORMAT PARQUET)', [str(source)])
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        preview = client.post('/api/v1/dataset-previews',
                              files={'file': ('renamed.parquet', source.read_bytes())})
        assert preview.status_code == 202, preview.text
        worker = Supervisor(Store(url), Launcher(), tmp_path / 'jobs',
                            'v2-parquet-worker', data_dir=tmp_path)
        assert worker.run_once()
        inspected = client.get(f"/api/v1/dataset-previews/{preview.json()['id']}").json()
        assert inspected['state'] == 'previewed'
        assert inspected['preview']['headers'] == [f'source_{role}' for role in SCHEMA]
        assert inspected['preview']['declared_types']['source_order_amount'] == 'DECIMAL(18,2)'

        contract = client.post('/api/v1/dataset-contracts',
                               json={**CONTRACT, 'null_policy': 'native_null'}).json()
        mapping = client.post('/api/v1/column-mappings', json={
            'contract_id': contract['id'],
            'columns': {role: f'source_{role}' for role in SCHEMA}}).json()
        uploaded = client.post('/api/v1/mapped-snapshots',
                               files={'file': ('renamed.parquet', source.read_bytes())},
                               data={'mapping_id': mapping['id'], 'name': 'Parquet sales'})
        assert uploaded.status_code == 202, uploaded.text
        assert worker.run_once()
        imported = client.get(f"/api/v1/snapshot-imports/{uploaded.json()['id']}").json()
        assert imported['state'] == 'validated', imported.get('error')
        snapshot = client.get(f"/api/v1/snapshots/{imported['snapshot_id']}").json()
        assert snapshot['row_count'] == 8
        assert snapshot['contract_hash'] == contract['contract_hash']
        assert snapshot['mapping_hash'] == mapping['mapping_hash']
