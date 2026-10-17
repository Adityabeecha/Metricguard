from fastapi.testclient import TestClient

from services.api.app import create_app


CONTRACT = {
    'version': 3, 'mode': 'flat_snapshot', 'name': 'Values by item',
    'row_key': 'item_key', 'measure': 'recorded_value', 'precision': 18,
    'scale': 2, 'categories': ['state'], 'measure_meaning': 'Recorded value',
    'unit_label': 'source units', 'values_comparable': True,
    'scope': 'entire_supplied_snapshot', 'null_policy': 'blank_nullable',
}


def test_flat_contract_and_definition_are_separate_from_retail(tmp_path):
    with TestClient(create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}",
                               data_dir=tmp_path, mode='isolated')) as client:
        response = client.post('/api/v1/flat/contracts', json=CONTRACT)
        assert response.status_code == 201, response.text
        contract = response.json()
        assert contract['execution_policy'] == 'flat_snapshot_v1'
        assert contract['contract_hash']
        definition = client.post('/api/v1/flat/definitions', json={
            'name': 'All values', 'contract_id': contract['id'],
            'raw_sql': 'SELECT SUM(recorded_value) FROM snapshot'})
        assert definition.status_code == 201, definition.text
        version = definition.json()['versions'][0]
        assert version['execution_policy'] == 'flat_snapshot_v1'
        assert version['contract_hash'] == contract['contract_hash']
        assert client.get('/api/v1/bootstrap').json()['flat_contracts'][0]['id'] == contract['id']


def test_flat_definition_refuses_unknown_column(tmp_path):
    with TestClient(create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}",
                               data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/flat/contracts', json=CONTRACT).json()
        response = client.post('/api/v1/flat/definitions', json={
            'name': 'Bad', 'contract_id': contract['id'],
            'raw_sql': 'SELECT SUM(other_value) FROM snapshot'})
        assert response.status_code == 422
