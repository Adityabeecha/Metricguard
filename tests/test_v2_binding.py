from fastapi.testclient import TestClient

from packages.metricguard_core.v2_contract import ingest_mapped, validate_contract, validate_mapping
from packages.metricguard_core.v2_binding import bind_sql
from services.api.app import create_app
from services.api.store import Store
from tests.test_v2_contract import CONTRACT, MAPPING, source_file


def test_binding_preserves_source_sql_and_only_allows_mapped_columns(tmp_path):
    with TestClient(create_app(database_url=f"sqlite:///{tmp_path / 'app.db'}",
                               data_dir=tmp_path, mode='isolated')) as client:
        contract = client.post('/api/v1/dataset-contracts', json=CONTRACT).json()
        mapping = client.post('/api/v1/column-mappings', json={
            'contract_id': contract['id'], **MAPPING}).json()
        raw = "SELECT SUM(gross_inr) FROM orders WHERE sale_state = 'completed'"
        created = client.post('/api/v1/definitions', json={
            'name': 'Completed gross', 'raw_sql': raw, 'time_column': 'sold_on',
            'mapping_id': mapping['id']})
        assert created.status_code == 201, created.text
        version = created.json()['versions'][0]
        assert version['raw_sql'] == raw
        assert 'order_amount' in version['bound_sql']
        assert version['contract_hash'] == contract['contract_hash']
        assert version['mapping_hash'] == mapping['mapping_hash']
        assert version['ir']['expression']['name'] == 'order_amount'
        assert version['ir']['time_column'] == 'order_date'

        starter = client.post('/api/v1/definitions', json={
            'name': 'Amount after refunds',
            'raw_sql': 'SELECT SUM("gross_inr" - COALESCE("returned_inr", 0)) FROM orders',
            'time_column': 'sold_on', 'mapping_id': mapping['id']})
        assert starter.status_code == 201, starter.text
        assert starter.json()['versions'][0]['support_state'] == 'executable'

        rejected = client.post('/api/v1/definitions', json={
            'name': 'Unknown', 'raw_sql': 'SELECT SUM(unknown_amount) FROM orders',
            'time_column': 'sold_on', 'mapping_id': mapping['id']})
        assert rejected.status_code == 422
        assert rejected.json()['error']['code'] == 'UNSUPPORTED_SQL'


def test_binder_refuses_unmapped_identifier():
    mapping = {'columns': MAPPING['columns'], 'contract_hash': 'a' * 64}
    with __import__('pytest').raises(Exception):
        bind_sql('SELECT SUM(other_amount) FROM orders', mapping)


def test_comparison_requires_same_contract_and_mapping(tmp_path):
    source = tmp_path / 'source.csv'
    source_file(source)
    contract = validate_contract(CONTRACT)
    mapping = validate_mapping(MAPPING, contract)
    snapshot_file = tmp_path / 'snapshot.duckdb'
    manifest = ingest_mapped(source, snapshot_file, contract, mapping)
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    store.record_dataset_contract(CONTRACT)
    approved = store.record_column_mapping(store.bootstrap()['dataset_contracts'][0]['id'], MAPPING)
    snapshot = store.record_snapshot('Mapped', manifest, snapshot_file)
    with TestClient(create_app(database_url=url, data_dir=tmp_path, mode='isolated')) as client:
        def definition(name, sql, mapping_id):
            response = client.post('/api/v1/definitions', json={
                'name': name, 'raw_sql': sql, 'time_column': 'sold_on',
                'mapping_id': mapping_id})
            assert response.status_code == 201, response.text
            return response.json()['versions'][0]['id']
        left = definition('Completed', "SELECT SUM(gross_inr) FROM orders WHERE sale_state = 'completed'", approved['id'])
        right = definition('Paid', "SELECT SUM(gross_inr) FROM orders WHERE pay_state = 'paid'", approved['id'])
        body = {'baseline_version_id': left, 'target_version_id': right,
                'snapshot_id': snapshot['id'], 'context': {
                    'start': '2026-08-01', 'end_exclusive': '2026-09-01',
                    'currency': 'INR', 'timezone': 'Asia/Kolkata', 'unit': 'currency',
                    'scope': 'retail_pilot', 'grain': 'one_row_per_sale',
                    'contract_hash': contract['contract_hash'],
                    'mapping_hash': mapping['mapping_hash']}}
        compatible = client.post('/api/v1/comparisons/validate', json=body)
        assert compatible.status_code == 200, compatible.text

        second = client.post('/api/v1/column-mappings', json={
            'contract_id': store.bootstrap()['dataset_contracts'][0]['id'],
            'columns': {**MAPPING['columns'], 'status': 'different_state'}}).json()
        changed = definition('Changed mapping', 'SELECT SUM(gross_inr) FROM orders', second['id'])
        mismatch = client.post('/api/v1/comparisons/validate', json={
            **body, 'target_version_id': changed})
        assert mismatch.status_code == 422
        assert mismatch.json()['error']['code'] == 'NOT_COMPARABLE'
