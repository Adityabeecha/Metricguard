"""Comprehensive verification suite for Milestone V4-01: dbt Artifact Import, Stable IDs & DAG Lineage."""
import json
import pytest
from uuid import uuid4

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.metadata_adapters import (
    AdapterError,
    MAX_DBT_MANIFEST_BYTES,
    MAX_DBT_RESOURCES,
    MAX_DBT_RUN_RESULTS_BYTES,
    analyze_dbt_sql,
    parse_dbt_project_artifacts,
    parse_run_results,
)
from services.api.store import Store


def _sample_manifest(version: int, models: dict | None = None, metrics: dict | None = None,
                     child_map: dict | None = None, invocation_id: str = 'inv-123') -> dict:
    compiled_key = 'compiled_code' if version >= 9 else 'compiled_sql'
    default_models = {
        'model.retail.executable_kpi': {
            'unique_id': 'model.retail.executable_kpi',
            'resource_type': 'model',
            'name': 'executable_kpi',
            'original_file_path': 'models/kpi.sql',
            'raw_code': 'SELECT SUM(order_amount) FROM {{ ref("orders") }}',
            compiled_key: "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': ['source.retail.raw_orders']},
        }
    }
    return {
        'metadata': {
            'dbt_schema_version': f'https://schemas.getdbt.com/dbt/manifest/v{version}.json',
            'dbt_version': f'1.{version}.0',
            'project_name': 'retail_analytics',
            'generated_at': '2026-09-25T12:00:00Z',
            'invocation_id': invocation_id,
        },
        'nodes': models if models is not None else default_models,
        'metrics': metrics or {},
        'child_map': child_map or {},
    }


# ===========================================================================
# 1. Schema Versions 7 to 12
# ===========================================================================

@pytest.mark.parametrize('v', [7, 8, 9, 10, 11, 12])
def test_manifest_schema_versions_7_to_12(v):
    artifact = _sample_manifest(version=v)
    raw = json.dumps(artifact).encode()
    parsed = parse_dbt_project_artifacts(raw)
    assert parsed['metadata']['schema_version'] == v
    assert parsed['metadata']['project_name'] == 'retail_analytics'
    assert len(parsed['resources']) == 1
    res = parsed['resources'][0]
    assert res['unique_id'] == 'model.retail.executable_kpi'
    assert res['support_state'] == 'executable'
    assert res['compiled_sql_present'] is True
    assert res['observed_lineage']['upstream_nodes'] == ['source.retail.raw_orders']


# ===========================================================================
# 2. Missing Compiled SQL & Unrendered Jinja
# ===========================================================================

def test_missing_compiled_sql_classifies_as_metadata_only():
    artifact = _sample_manifest(version=12, models={
        'model.retail.uncompiled': {
            'unique_id': 'model.retail.uncompiled',
            'resource_type': 'model',
            'name': 'uncompiled',
            'original_file_path': 'models/raw.sql',
            'raw_code': 'SELECT * FROM {{ ref("orders") }}',
            'depends_on': {'nodes': []},
        }
    })
    parsed = parse_dbt_project_artifacts(json.dumps(artifact).encode())
    res = parsed['resources'][0]
    assert res['compiled_sql_present'] is False
    assert res['compiled_sql'] is None
    assert res['support_state'] == 'metadata_only'
    assert any('MISSING_COMPILED_SQL' in d for d in res['diagnostics'])


def test_unrendered_jinja_classifies_as_metadata_only():
    artifact = _sample_manifest(version=12, models={
        'model.retail.jinja_leak': {
            'unique_id': 'model.retail.jinja_leak',
            'resource_type': 'model',
            'name': 'jinja_leak',
            'original_file_path': 'models/leak.sql',
            'raw_code': 'SELECT {{ macro() }} FROM orders',
            'compiled_code': 'SELECT {{ macro() }} FROM orders',
            'depends_on': {'nodes': []},
        }
    })
    parsed = parse_dbt_project_artifacts(json.dumps(artifact).encode())
    res = parsed['resources'][0]
    assert res['compiled_sql_present'] is False
    assert res['compiled_sql'] is None
    assert res['support_state'] == 'metadata_only'
    assert any('UNRENDERED_JINJA' in d for d in res['diagnostics'])


# ===========================================================================
# 3. CTE and JOIN Detection (Degrades Gracefully to metadata_only)
# ===========================================================================

def test_cte_detection_classifies_as_metadata_only():
    cte_sql = "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b"
    present, state, diags = analyze_dbt_sql(cte_sql)
    assert present is True
    assert state == 'metadata_only'
    assert any('CTE_MULTIPLE' in d for d in diags)


def test_join_detection_classifies_as_metadata_only():
    join_sql = "SELECT SUM(o.order_amount) FROM orders o JOIN customers c ON o.customer_id = c.id"
    present, state, diags = analyze_dbt_sql(join_sql)
    assert present is True
    assert state == 'metadata_only'
    assert any('JOIN_DETECTED' in d for d in diags)


def test_multiple_tables_in_from_classifies_as_metadata_only():
    multi_sql = "SELECT SUM(order_amount) FROM orders, customers"
    present, state, diags = analyze_dbt_sql(multi_sql)
    assert present is True
    assert state == 'metadata_only'
    assert any(('JOIN_DETECTED' in d or 'MULTIPLE_TABLES_DETECTED' in d) for d in diags)


# ===========================================================================
# 4. Companion run_results.json Correlation
# ===========================================================================

def test_companion_run_results_correlation():
    manifest_obj = _sample_manifest(version=12)
    run_results_obj = {
        'metadata': {
            'dbt_schema_version': 'https://schemas.getdbt.com/dbt/run-results/v4.json',
            'dbt_version': '1.12.0',
            'invocation_id': 'inv-123',
        },
        'results': [
            {
                'unique_id': 'model.retail.executable_kpi',
                'status': 'success',
                'execution_time': 0.42,
                'message': 'SUCCESS 1',
                'failures': None,
            }
        ]
    }
    parsed = parse_dbt_project_artifacts(
        manifest_content=json.dumps(manifest_obj).encode(),
        run_results_content=json.dumps(run_results_obj).encode()
    )
    res = parsed['resources'][0]
    assert res['run_results_status'] is not None
    assert res['run_results_status']['status'] == 'success'
    assert res['run_results_status']['execution_time'] == 0.42
    assert res['run_results_status']['message'] == 'SUCCESS 1'


# ===========================================================================
# 5. Store Persistence: DefinitionVersion Reuse (Correction 1 Invariant)
# ===========================================================================

def test_dbt_import_reuses_definition_version_when_sql_unchanged(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Manifest Rev 1: Git SHA 1, initial executable model
    manifest_v1 = _sample_manifest(version=12, invocation_id='inv-001')
    res1 = store.record_dbt_import(
        manifest_content=json.dumps(manifest_v1).encode(),
        source_revision={'git_sha': 'commit-aaa-111', 'branch': 'main'},
        project_id=pid
    )
    assert res1['is_reimport'] is False
    assert len(res1['resources']) == 1
    r1 = res1['resources'][0]
    defn_id_1 = r1['definition_id']
    version_id_1 = r1['definition_version_id']
    assert defn_id_1 is not None
    assert version_id_1 is not None

    # Check store has 1 definition and 1 version
    defn_record = store.definition(defn_id_1)
    assert len(defn_record['versions']) == 1
    assert defn_record['versions'][0]['id'] == version_id_1

    # Manifest Rev 2: Git SHA 2, new invocation, SAME compiled SQL
    manifest_v2 = _sample_manifest(version=12, invocation_id='inv-002')
    res2 = store.record_dbt_import(
        manifest_content=json.dumps(manifest_v2).encode(),
        source_revision={'git_sha': 'commit-bbb-222', 'branch': 'main'},
        project_id=pid
    )
    assert res2['is_reimport'] is False
    assert res2['id'] != res1['id']  # New DbtImportRevision created
    r2 = res2['resources'][0]
    assert r2['id'] != r1['id']  # New DbtResourceRevision created

    # CRUCIAL INVARIANT: DefinitionVersion MUST BE REUSED, NOT DUPLICATED!
    assert r2['definition_id'] == defn_id_1
    assert r2['definition_version_id'] == version_id_1

    # Verify no second version was created in Store
    defn_record = store.definition(defn_id_1)
    assert len(defn_record['versions']) == 1
    assert defn_record['versions'][0]['version_number'] == 1


def test_dbt_import_creates_new_definition_version_when_sql_changes(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Manifest Rev 1
    manifest_v1 = _sample_manifest(version=12, invocation_id='inv-001')
    res1 = store.record_dbt_import(
        manifest_content=json.dumps(manifest_v1).encode(),
        source_revision={'git_sha': 'commit-aaa-111'},
        project_id=pid
    )
    r1 = res1['resources'][0]
    defn_id = r1['definition_id']
    v1_id = r1['definition_version_id']

    # Manifest Rev 2: Modified SQL (added status filter)
    modified_sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"
    manifest_v2 = _sample_manifest(version=12, invocation_id='inv-002', models={
        'model.retail.executable_kpi': {
            'unique_id': 'model.retail.executable_kpi',
            'resource_type': 'model',
            'name': 'executable_kpi',
            'original_file_path': 'models/kpi.sql',
            'raw_code': 'SELECT ...',
            'compiled_code': modified_sql,
            'depends_on': {'nodes': ['source.retail.raw_orders']},
        }
    })
    res2 = store.record_dbt_import(
        manifest_content=json.dumps(manifest_v2).encode(),
        source_revision={'git_sha': 'commit-ccc-333'},
        project_id=pid
    )
    r2 = res2['resources'][0]
    assert r2['definition_id'] == defn_id
    assert r2['definition_version_id'] != v1_id

    # Verify Store now has 2 versions under the same Definition identity
    defn_record = store.definition(defn_id)
    assert len(defn_record['versions']) == 2
    assert defn_record['versions'][0]['version_number'] == 1
    assert defn_record['versions'][1]['version_number'] == 2
    assert defn_record['versions'][1]['id'] == r2['definition_version_id']


# ===========================================================================
# 6. Revision-Aware DAG Lineage
# ===========================================================================

def test_revision_aware_dag_lineage(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Rev 1: upstream is raw_orders
    manifest_v1 = _sample_manifest(version=12, invocation_id='inv-001', models={
        'model.retail.executable_kpi': {
            'unique_id': 'model.retail.executable_kpi',
            'resource_type': 'model',
            'name': 'executable_kpi',
            'original_file_path': 'models/kpi.sql',
            'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': ['source.retail.raw_orders']},
        }
    }, child_map={'model.retail.executable_kpi': ['model.retail.executive_summary']})

    res1 = store.record_dbt_import(json.dumps(manifest_v1).encode(), project_id=pid)
    rev1_id = res1['id']

    # Rev 2: upstream changes to stg_orders, SQL is identical
    manifest_v2 = _sample_manifest(version=12, invocation_id='inv-002', models={
        'model.retail.executable_kpi': {
            'unique_id': 'model.retail.executable_kpi',
            'resource_type': 'model',
            'name': 'executable_kpi',
            'original_file_path': 'models/kpi.sql',
            'compiled_code': "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
            'depends_on': {'nodes': ['model.retail.stg_orders']},
        }
    }, child_map={'model.retail.executable_kpi': []})

    res2 = store.record_dbt_import(json.dumps(manifest_v2).encode(), project_id=pid)
    rev2_id = res2['id']

    # Check lineage queries
    lineage1 = store.get_dbt_lineage('model.retail.executable_kpi', import_revision_id=rev1_id, project_id=pid)
    assert lineage1['observed_lineage']['upstream_nodes'] == ['source.retail.raw_orders']
    assert lineage1['observed_lineage']['downstream_nodes'] == ['model.retail.executive_summary']

    lineage2 = store.get_dbt_lineage('model.retail.executable_kpi', import_revision_id=rev2_id, project_id=pid)
    assert lineage2['observed_lineage']['upstream_nodes'] == ['model.retail.stg_orders']
    assert lineage2['observed_lineage']['downstream_nodes'] == []


# ===========================================================================
# 7. Idempotent Re-Import
# ===========================================================================

def test_idempotent_reimport(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    raw = json.dumps(_sample_manifest(version=12)).encode()

    res1 = store.record_dbt_import(raw, project_id=pid)
    assert res1['is_reimport'] is False

    res2 = store.record_dbt_import(raw, project_id=pid)
    assert res2['is_reimport'] is True
    assert res2['id'] == res1['id']
    assert len(res2['resources']) == len(res1['resources'])

    # Verify no duplicate import records in Store
    all_revs = store.list_dbt_import_revisions(pid)
    assert len(all_revs) == 1


# ===========================================================================
# 8. Guardrails and Safety Limits
# ===========================================================================

def test_guardrails_manifest_size_and_schema_version():
    # Size check
    oversized = b' ' * (MAX_DBT_MANIFEST_BYTES + 1)
    with pytest.raises(AdapterError, match='exceeds'):
        parse_dbt_project_artifacts(oversized)

    # Schema version check
    bad_schema = {'metadata': {'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v6.json'}}
    with pytest.raises(AdapterError, match='version'):
        parse_dbt_project_artifacts(json.dumps(bad_schema).encode())

    # Resources limit check
    too_many_nodes = {f'model.x.m{i}': {'unique_id': f'model.x.m{i}', 'resource_type': 'model'} for i in range(MAX_DBT_RESOURCES + 1)}
    oversized_nodes = {'metadata': {'dbt_schema_version': 'https://schemas.getdbt.com/dbt/manifest/v12.json'}, 'nodes': too_many_nodes, 'metrics': {}}
    with pytest.raises(AdapterError, match='limited'):
        parse_dbt_project_artifacts(json.dumps(oversized_nodes).encode())


# ===========================================================================
# 9. API Endpoint, RBAC and Multi-Tenancy Tests
# ===========================================================================

def test_api_dbt_import_rbac_and_tenancy(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    store = Store(db_url)
    app = create_app(database_url=db_url, data_dir=tmp_path / 'data', mode='demo')
    client = TestClient(app)

    # Create users
    analyst, analyst_key = store.create_user('Analyst User', 'analyst@test.com')
    reader, reader_key = store.create_user('Reader User', 'reader@test.com')

    # Project A
    proj_a = store.create_project('Project Alpha', project_id='proj-alpha')
    store.add_project_member('proj-alpha', analyst['id'], 'analyst')
    store.add_project_member('proj-alpha', reader['id'], 'reader')

    manifest_bytes = json.dumps(_sample_manifest(version=12)).encode()

    # Reader cannot upload (403 Forbidden)
    res = client.post(
        '/api/v1/projects/proj-alpha/dbt/imports',
        headers={'Authorization': f'Bearer {reader_key}'},
        files={'manifest': ('manifest.json', manifest_bytes, 'application/json')}
    )
    assert res.status_code == 403

    # Analyst can upload (201 Created)
    res = client.post(
        '/api/v1/projects/proj-alpha/dbt/imports',
        headers={'Authorization': f'Bearer {analyst_key}'},
        files={'manifest': ('manifest.json', manifest_bytes, 'application/json')},
        data={'git_sha': 'sha-alpha-123', 'branch': 'feat/kpi'}
    )
    assert res.status_code == 201
    body = res.json()
    assert body['source_revision']['git_sha'] == 'sha-alpha-123'
    assert body['source_revision']['branch'] == 'feat/kpi'
    rev_id = body['id']

    # Reader CAN list revisions
    res = client.get(
        '/api/v1/projects/proj-alpha/dbt/revisions',
        headers={'Authorization': f'Bearer {reader_key}'}
    )
    assert res.status_code == 200
    assert len(res.json()['items']) == 1

    # Reader CAN view revision detail
    res = client.get(
        f'/api/v1/projects/proj-alpha/dbt/revisions/{rev_id}',
        headers={'Authorization': f'Bearer {reader_key}'}
    )
    assert res.status_code == 200
    assert res.json()['id'] == rev_id

    # Cross-project isolation: Project B user cannot access Project A revisions
    user_b, key_b = store.create_user('User Beta', 'user_b@test.com')
    proj_b = store.create_project('Project Beta', project_id='proj-beta')
    store.add_project_member('proj-beta', user_b['id'], 'analyst')

    res = client.get(
        f'/api/v1/projects/proj-alpha/dbt/revisions/{rev_id}',
        headers={'Authorization': f'Bearer {key_b}'}
    )
    assert res.status_code == 403
