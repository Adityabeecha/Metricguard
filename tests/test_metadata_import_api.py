import io
import json

from fastapi.testclient import TestClient

from services.api.app import create_app


def test_dbt_and_tmdl_uploads_are_persisted_as_metadata_only(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}", data_dir=tmp_path, mode='demo')
    manifest = {'metadata': {'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v12.json'},
                'nodes': {'model.x.orders': {'unique_id': 'model.x.orders', 'resource_type': 'model',
                                            'name': 'orders', 'compiled_code': 'SELECT * FROM orders'}}}
    with TestClient(app) as client:
        dbt = client.post('/api/v1/metadata-imports/dbt', files={'file': ('manifest.json', io.BytesIO(json.dumps(manifest).encode()), 'application/json')})
        assert dbt.status_code == 201
        assert dbt.json()['format'] == 'dbt_manifest'
        assert dbt.json()['capabilities']['execute_natively'] is False
        tmdl = client.post('/api/v1/metadata-imports/tmdl', files={'file': ('model.tmdl', io.BytesIO(b'table Sales\n    measure Revenue = SUM(Sales[Amount])'), 'text/plain')})
        assert tmdl.status_code == 201
        assert tmdl.json()['measures'][0]['support_state'] == 'metadata_only'
        bootstrap = client.get('/api/v1/bootstrap').json()
        assert len(bootstrap['metadata_imports']) == 2
        assert len(bootstrap['definitions']) == 0


def test_metadata_upload_rejects_invalid_artifacts(tmp_path):
    app = create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}", data_dir=tmp_path, mode='demo')
    with TestClient(app) as client:
        bad = client.post('/api/v1/metadata-imports/dbt', files={'file': ('manifest.json', b'{}', 'application/json')})
        assert bad.status_code == 422
        assert bad.json()['error']['code'] == 'INVALID_METADATA_ARTIFACT'
        wrong_ext = client.post('/api/v1/metadata-imports/tmdl', files={'file': ('model.exe', b'table Sales', 'application/octet-stream')})
        assert wrong_ext.status_code == 422
