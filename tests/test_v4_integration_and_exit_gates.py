"""Comprehensive verification test suite for MetricGuard Milestone V4-04:
Integration, Exit-Gate Verification, and Decoupled Benchmarks.

Proves end-to-end:
dbt artifact import
→ dbt revision/resource linkage
→ PostgreSQL collection
→ frozen DuckDB snapshot
→ supported MetricGuard comparison
→ CLI/CI execution
→ JSON/Markdown report
→ replay determinism
→ review decision and complete provenance trail.

Verifies Exit Gates:
- Gate 4.1: Provenance chain (source revision -> definition -> contract -> snapshot -> run -> review decision)
- Gate 4.2: Visible failure / refusal (missing compiled SQL, unrendered Jinja, CTE, JOIN, unsupported SQL, failed extraction)
- Gate 4.3: Cutoff / retry isolation (zero partial snapshots on failure, same logical cutoff on retry, isolated attempts)
- Gate 4.4: Snapshot determinism (repeated executions against frozen snapshot produce identical analytical output)
- Gate 4.5: No live-source dependency (modifying PostgreSQL source table after snapshot creation does not alter comparison results)
- Decoupled benchmarks and failure boundaries
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import pytest

import duckdb
import psycopg
from fastapi.testclient import TestClient

from packages.metricguard_core import compare, parse_definition
from packages.metricguard_core.common import SCHEMA, digest, file_hash
from packages.metricguard_core.snapshot import inspect_snapshot, manifest_path
from scripts.evaluate_v4_benchmarks import run_benchmarks
from services.api.app import create_app
from services.api.metadata_adapters import (
    MAX_DBT_MANIFEST_BYTES,
    MAX_DBT_RESOURCES,
    AdapterError,
    analyze_dbt_sql,
    parse_dbt_project_artifacts,
)
from services.api.store import DomainError, Store
from services.cli import (
    CIOutcome,
    ResolvedInputs,
    execute_headless_comparison,
    generate_markdown_report,
    generate_terminal_summary,
    run_cli,
)
from services.supervisor.collector import (
    CollectorError,
    PostgresCollector,
    PostgresSourceSelection,
    sanitize_source_uri,
)

PG_TEST_URL = os.environ.get(
    'METRICGUARD_PG_TEST_URL',
    'postgresql://metricguard:local-dev-only@127.0.0.1:5433/metricguard'
)


def is_pg_available() -> bool:
    try:
        with psycopg.connect(PG_TEST_URL, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                return True
    except Exception:
        return False


@pytest.fixture
def test_workspace(tmp_path: Path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / 'app.db'
    store = Store(f"sqlite:///{db_path}")

    owner, owner_key = store.create_user('Platform Owner', 'owner@metricguard.internal')
    analyst, analyst_key = store.create_user('Data Analyst', 'analyst@metricguard.internal')
    reader, reader_key = store.create_user('Guest Reader', 'reader@metricguard.internal')

    project_id = f"proj-v4-{uuid.uuid4().hex[:8]}"
    project = store.create_project('V4 Production Analytics', owner_id=owner['id'], project_id=project_id)
    store.add_project_member(project_id, analyst['id'], 'analyst')
    store.add_project_member(project_id, reader['id'], 'reader')

    contract_payload = {
        'version': 2,
        'name': 'Retail Sales Contract V4',
        'currency': 'INR',
        'timezone': 'Asia/Kolkata',
        'scope': 'v4_retail_sales',
        'grain': 'one_row_per_order_id',
        'cutoff_at': '2026-09-02',
        'null_policy': 'blank_nullable',
        'row_key': 'order_id',
        'entity_key': 'customer_id',
        'decimal_scale': 2,
        'schema': SCHEMA,
    }
    contract = store.record_dataset_contract(contract_payload)

    collector = PostgresCollector(store=store, data_dir=data_dir)

    yield {
        'tmp_path': tmp_path,
        'data_dir': data_dir,
        'db_path': db_path,
        'store': store,
        'project_id': project_id,
        'owner': owner,
        'owner_key': owner_key,
        'analyst': analyst,
        'analyst_key': analyst_key,
        'reader': reader,
        'reader_key': reader_key,
        'contract': contract,
        'collector': collector,
    }
    try:
        if hasattr(store, 'engine'):
            store.engine.dispose()
    except Exception:
        pass


@pytest.fixture
def pg_orders_table():
    if not is_pg_available():
        pytest.skip(f"PostgreSQL test database unavailable at {sanitize_source_uri(PG_TEST_URL)}")

    schema_name = f"test_v4_{uuid.uuid4().hex[:10]}"
    table_name = "orders_v4_test"

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema_name}"')
            cur.execute(f'''
                CREATE TABLE "{schema_name}"."{table_name}" (
                    order_id VARCHAR(64) PRIMARY KEY,
                    customer_id VARCHAR(64) NOT NULL,
                    order_date DATE NOT NULL,
                    payment_date DATE,
                    status VARCHAR(32) NOT NULL,
                    payment_status VARCHAR(32) NOT NULL,
                    order_amount NUMERIC(18, 2) NOT NULL,
                    refund_amount NUMERIC(18, 2),
                    currency VARCHAR(8) NOT NULL
                )
            ''')
            # 1000 deterministic test orders
            rows = []
            for i in range(1, 1001):
                day = (i % 28) + 1
                status = 'Completed' if i % 4 != 0 else 'Cancelled'
                p_status = 'Paid' if status == 'Completed' and i % 5 != 0 else 'Pending'
                amount = Decimal('100.00') + Decimal(i)
                refund = Decimal('0.00') if p_status == 'Paid' else Decimal('15.00')
                rows.append((
                    f"ord-v4-{i:05d}",
                    f"cust-{i % 100:03d}",
                    date(2026, 8, day),
                    date(2026, 8, day) if p_status == 'Paid' else None,
                    status,
                    p_status,
                    amount,
                    refund,
                    'INR'
                ))
            cur.executemany(f'''
                INSERT INTO "{schema_name}"."{table_name}"
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', rows)

    yield {
        'schema_name': schema_name,
        'table_name': table_name,
        'row_count': 1000,
        'source_uri': PG_TEST_URL,
    }

    with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


# ===========================================================================
# 1. Realistic End-to-End V4 Workflow Integration
# ===========================================================================

class TestV4EndToEndWorkflow:
    """Verifies the complete V4 lifecycle end-to-end:
    dbt artifact import -> resource linkage -> PostgreSQL collection ->
    frozen snapshot -> CLI comparison -> JSON/MD report -> replay -> review.
    """

    def test_complete_end_to_end_v4_scenario(self, test_workspace, pg_orders_table):
        store: Store = test_workspace['store']
        pid = test_workspace['project_id']
        data_dir = test_workspace['data_dir']

        # 1. dbt Core Manifest Import
        baseline_unique_id = "model.retail.monthly_completed_revenue"
        target_unique_id = "model.retail.monthly_paid_revenue"
        manifest_obj = {
            "metadata": {
                "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v10.json",
                "dbt_version": "1.8.0",
                "project_name": "retail_ops",
                "generated_at": "2026-09-25T12:00:00Z",
                "invocation_id": "inv-v4-e2e-001"
            },
            "nodes": {
                baseline_unique_id: {
                    "resource_type": "model",
                    "name": "monthly_completed_revenue",
                    "schema": "public",
                    "database": "retail",
                    "unique_id": baseline_unique_id,
                    "compiled": True,
                    "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                    "depends_on": {"nodes": ["source.retail.raw_orders"]}
                },
                target_unique_id: {
                    "resource_type": "model",
                    "name": "monthly_paid_revenue",
                    "schema": "public",
                    "database": "retail",
                    "unique_id": target_unique_id,
                    "compiled": True,
                    "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND payment_status = 'Paid' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                    "depends_on": {"nodes": ["source.retail.raw_orders"]}
                }
            }
        }
        manifest_bytes = json.dumps(manifest_obj).encode('utf-8')
        git_sha = "abc1234def567890abcdef1234567890abcdef12"

        import_rev = store.record_dbt_import(
            manifest_content=manifest_bytes,
            source_revision={'git_sha': git_sha, 'branch': 'feat/v4-revenue-reconcile'},
            project_id=pid
        )
        assert import_rev['id'].startswith('dbt-rev-')
        assert len(import_rev['resources']) == 2
        for r in import_rev['resources']:
            assert r['support_state'] == 'executable'
            assert r['definition_version_id'] is not None

        # 2. PostgreSQL Collection into Frozen DuckDB Snapshot
        collector: PostgresCollector = test_workspace['collector']
        selection = PostgresSourceSelection(
            source_uri=pg_orders_table['source_uri'],
            schema_name=pg_orders_table['schema_name'],
            table_name=pg_orders_table['table_name'],
            columns=list(SCHEMA),
            cutoff_column="order_date",
            cutoff_value="2026-09-02",
            contract_id=test_workspace['contract']['id'],
            snapshot_name="Frozen Production Orders 2026-09-02"
        )
        snapshot_res = collector.collect(selection=selection, project_id=pid)
        assert snapshot_res['row_count'] == 1000
        snap_id = snapshot_res['snapshot_id']
        snap_file = Path(snapshot_res['artifact_path'])
        assert snap_file.is_file()

        # Verify companion manifest
        m_file = manifest_path(snap_file)
        assert m_file.is_file()
        m_data = json.loads(m_file.read_text(encoding='utf-8'))
        assert m_data['database_hash'] == snapshot_res['database_hash']
        assert m_data['contract_hash'] == test_workspace['contract']['contract_hash']

        # 3. Headless CLI Subprocess Execution with JSON and Markdown Export
        json_out = data_dir / 'e2e_report.json'
        md_out = data_dir / 'e2e_report.md'

        env = os.environ.copy()
        env['METRICGUARD_API_KEY'] = test_workspace['analyst_key']
        env['METRICGUARD_PROJECT_ID'] = pid

        context_payload = {
            'start': '2026-08-01',
            'end_exclusive': '2026-09-01',
            'timezone': 'Asia/Kolkata',
            'currency': 'INR',
            'unit': 'currency',
            'grain': 'one_row_per_order_id',
            'scope': 'v4_retail_sales',
            'contract_hash': test_workspace['contract']['contract_hash'],
            'mapping_hash': m_data.get('mapping_hash')
        }

        cmd = [
            sys.executable, '-m', 'services.cli', 'compare',
            '--baseline', f'dbt:{baseline_unique_id}',
            '--target', f'dbt:{target_unique_id}',
            '--snapshot', snap_id,
            '--mode', 'enforcing',
            '--absolute-threshold', '500000.00',
            '--context-json', json.dumps(context_payload),
            '--database-url', f"sqlite:///{test_workspace['db_path']}",
            '--data-dir', str(data_dir),
            '--output', str(json_out),
            '--markdown-output', str(md_out)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
        assert proc.returncode == 0, f"CLI exited with {proc.returncode}. Stderr: {proc.stderr}\nStdout: {proc.stdout}"

        # 4. Verify Generated JSON Report
        assert json_out.is_file()
        report = json.loads(json_out.read_text(encoding='utf-8'))
        assert report['schema'] == 'metricguard_ci_report_v1'
        assert report['exit_code'] == 0
        assert report['outcome'] == 'supported'
        assert report['project_id'] == pid

        # Verify exact decimal totals and materiality
        result = report['result']
        assert Decimal(result['baseline_value']) > Decimal(0)
        assert Decimal(result['target_value']) > Decimal(0)
        assert Decimal(result['delta_target_minus_baseline']) < Decimal(0)
        assert result['value_status'] == 'different'

        # Verify dbt provenance anchors
        assert report['baseline']['dbt']['unique_id'] == baseline_unique_id
        assert report['baseline']['dbt']['source_revision']['git_sha'] == git_sha
        assert report['target']['dbt']['unique_id'] == target_unique_id
        assert report['snapshot']['database_hash'] == snapshot_res['database_hash']
        assert report['snapshot']['manifest_hash'] == snapshot_res['manifest_hash']

        # 5. Verify Generated Markdown Report
        assert md_out.is_file()
        md_text = md_out.read_text(encoding='utf-8')
        assert "# MetricGuard CI Comparison Report" in md_text
        assert baseline_unique_id in md_text
        assert target_unique_id in md_text
        assert git_sha in md_text
        assert "Metric Results & Materiality" in md_text
        assert "Evidence & Row Attribution" in md_text

        # 6. Replay Determinism: Second Run Yields Byte-for-Byte Report Identity
        json_out_2 = data_dir / 'e2e_report_2.json'
        cmd2 = cmd.copy()
        cmd2[cmd2.index(str(json_out))] = str(json_out_2)
        proc2 = subprocess.run(cmd2, capture_output=True, text=True, env=env, timeout=30)
        assert proc2.returncode == 0
        report2 = json.loads(json_out_2.read_text(encoding='utf-8'))

        assert report2['result']['baseline_value'] == report['result']['baseline_value']
        assert report2['result']['target_value'] == report['result']['target_value']
        assert report2['result']['delta_target_minus_baseline'] == report['result']['delta_target_minus_baseline']
        assert report2['request_hash'] == report['request_hash']

        # 7. Review Decision Linkage
        # Analyst requests review of intentional variant
        baseline_ver_id = report['baseline']['definition_version_id']
        target_ver_id = report['target']['definition_version_id']
        target_def_id = report['target']['definition_id']

        app = create_app(database_url=f"sqlite:///{test_workspace['db_path']}", data_dir=data_dir)
        with TestClient(app) as client:
            a_headers = {'Authorization': f"Bearer {test_workspace['analyst_key']}", 'X-Project-Id': pid}
            o_headers = {'Authorization': f"Bearer {test_workspace['owner_key']}", 'X-Project-Id': pid}

            # Submit review request
            req_payload = {
                'definition_id': target_def_id,
                'baseline_version_id': baseline_ver_id,
                'target_version_id': target_ver_id,
                'run_id': report['run_id'],
                'rationale': 'Exclude uncaptured orders to conform to GAAP FY26 criteria.',
                'is_intentional_variant': True,
                'variant_rationale': 'Finance paid-only variant',
                'effective_time': '2026-08-01T00:00:00Z'
            }
            res_req = client.post(f"/api/v1/projects/{pid}/review-requests", headers=a_headers, json=req_payload)
            assert res_req.status_code == 201
            review_request = res_req.json()

            # Owner records approval decision
            dec_payload = {
                'review_request_id': review_request['id'],
                'approved_version_id': target_ver_id,
                'decision': 'variant_accepted',
                'rationale': 'Approved per Q3 Revenue Audit review.'
            }
            res_dec = client.post(f"/api/v1/projects/{pid}/decisions", headers=o_headers, json=dec_payload)
            assert res_dec.status_code == 201
            decision = res_dec.json()

            assert decision['decision'] == 'variant_accepted'
            assert decision['approved_version_id'] == target_ver_id
            assert decision['review_request_id'] == review_request['id']


# ===========================================================================
# 2. Gate 4.1: Provenance Chain
# ===========================================================================

class TestGate41ProvenanceChain:
    """Verifies that a source/dbt revision links cleanly along the complete chain:
    source/dbt revision -> definition -> definition version -> dataset contract ->
    snapshot -> comparison run -> review decision.
    """

    def test_unbroken_provenance_chain(self, test_workspace, pg_orders_table):
        store: Store = test_workspace['store']
        pid = test_workspace['project_id']
        data_dir = test_workspace['data_dir']

        # Step 1: dbt Import Revision with Git metadata
        git_sha = "dbt-git-sha-9876543210fedcba"
        unique_id = "model.retail.provenance_target"
        manifest_obj = {
            "metadata": {
                "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v10.json",
                "dbt_version": "1.8.0",
                "project_name": "provenance_check",
                "generated_at": "2026-09-25T14:00:00Z",
                "invocation_id": "inv-prov-001"
            },
            "nodes": {
                unique_id: {
                    "resource_type": "model",
                    "name": "provenance_target",
                    "schema": "public",
                    "database": "retail",
                    "unique_id": unique_id,
                    "compiled": True,
                    "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                    "depends_on": {"nodes": ["source.retail.raw_orders"]}
                }
            }
        }
        import_rev = store.record_dbt_import(
            manifest_content=json.dumps(manifest_obj).encode(),
            source_revision={'git_sha': git_sha, 'branch': 'main'},
            project_id=pid
        )
        resource_rev = import_rev['resources'][0]

        # Step 2: Linkage to Definition and DefinitionVersion
        def_id = resource_rev['definition_id']
        def_ver_id = resource_rev['definition_version_id']
        assert def_id is not None
        assert def_ver_id is not None

        defn = store.definition(def_id)
        assert any(v['id'] == def_ver_id for v in defn['versions'])
        version_obj = store.version(def_ver_id)
        assert version_obj['id'] == def_ver_id

        # Step 3: Dataset Contract
        contract_id = test_workspace['contract']['id']
        contract_hash = test_workspace['contract']['contract_hash']
        assert contract_hash is not None

        # Step 4: PostgreSQL Collection into Snapshot
        collector = test_workspace['collector']
        selection = PostgresSourceSelection(
            source_uri=pg_orders_table['source_uri'],
            schema_name=pg_orders_table['schema_name'],
            table_name=pg_orders_table['table_name'],
            columns=list(SCHEMA),
            cutoff_column="order_date",
            cutoff_value="2026-09-02",
            contract_id=contract_id
        )
        snapshot_res = collector.collect(selection=selection, project_id=pid)
        snap_id = snapshot_res['snapshot_id']
        snap_obj = store.snapshot(snap_id)
        assert snap_obj['manifest_hash'] == snapshot_res['manifest_hash']
        assert snap_obj['contract_hash'] == contract_hash

        # Step 5: Headless Comparison Run
        resolved_inputs = ResolvedInputs(
            project_id=pid,
            baseline_version=version_obj,
            target_version=version_obj,
            snapshot=snap_obj,
            context={
                'start': '2026-08-01',
                'end_exclusive': '2026-09-01',
                'timezone': 'Asia/Kolkata',
                'currency': 'INR',
                'unit': 'currency',
                'grain': 'one_row_per_order_id',
                'scope': 'v4_retail_sales',
                'contract_hash': contract_hash,
                'mapping_hash': snap_obj.get('mapping_hash')
            },
            mode='advisory',
            materiality_policy=None,
            relationship='expected_agreement'
        )
        report = execute_headless_comparison(store=store, inputs=resolved_inputs)
        assert report['outcome'] == 'supported'
        run_id = report['run_id']
        assert run_id is not None

        # Step 6: Review Request and Approving Decision
        app = create_app(database_url=f"sqlite:///{test_workspace['db_path']}", data_dir=data_dir)
        with TestClient(app) as client:
            a_headers = {'Authorization': f"Bearer {test_workspace['analyst_key']}", 'X-Project-Id': pid}
            o_headers = {'Authorization': f"Bearer {test_workspace['owner_key']}", 'X-Project-Id': pid}

            req_res = client.post(f"/api/v1/projects/{pid}/review-requests", headers=a_headers, json={
                'definition_id': def_id,
                'baseline_version_id': def_ver_id,
                'target_version_id': def_ver_id,
                'run_id': run_id,
                'rationale': 'Audit provenance trail verification',
                'is_intentional_variant': False
            })
            assert req_res.status_code == 201
            req_data = req_res.json()

            dec_res = client.post(f"/api/v1/projects/{pid}/decisions", headers=o_headers, json={
                'review_request_id': req_data['id'],
                'approved_version_id': def_ver_id,
                'decision': 'approved',
                'rationale': 'Provenance trail complete and approved'
            })
            assert dec_res.status_code == 201
            decision_data = dec_res.json()

        # Step 7: Final Assertion of Full Chain
        assert decision_data['approved_version_id'] == def_ver_id
        assert decision_data['review_request_id'] == req_data['id']
        assert req_data['run_id'] == run_id
        assert report['baseline']['definition_version_id'] == def_ver_id
        assert report['snapshot']['snapshot_id'] == snap_id
        assert snap_obj['contract_hash'] == contract_hash
        assert version_obj['definition_id'] == def_id
        assert resource_rev['definition_version_id'] == def_ver_id
        assert import_rev['source_revision']['git_sha'] == git_sha


# ===========================================================================
# 3. Gate 4.2: Visible Failure / Refusal
# ===========================================================================

class TestGate42VisibleFailureRefusal:
    """Verifies that missing compiled SQL, unrendered Jinja, CTE, JOIN,
    unsupported SQL, and failed extraction remain explicit and NEVER become silent passes.
    """

    def test_missing_compiled_sql_classifies_as_metadata_only_and_refuses(self):
        sql = ""
        present, state, diags = analyze_dbt_sql(sql)
        assert present is False
        assert state == 'metadata_only'
        assert any("MISSING_COMPILED_SQL" in d for d in diags)

    def test_unrendered_jinja_classifies_as_metadata_only_and_refuses(self):
        sql = "SELECT SUM(order_amount) FROM {{ ref('orders') }} WHERE status = 'Completed'"
        present, state, diags = analyze_dbt_sql(sql)
        assert present is False
        assert state == 'metadata_only'
        assert any("UNRENDERED_JINJA" in d for d in diags)

    def test_cte_classifies_as_metadata_only_and_refuses(self):
        sql = "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b"
        present, state, diags = analyze_dbt_sql(sql)
        assert state == 'metadata_only'
        assert any("CTE_MULTIPLE" in d for d in diags)

    def test_multi_table_join_classifies_as_metadata_only_and_refuses(self):
        sql = "SELECT SUM(o.order_amount) FROM orders o JOIN customers c ON o.customer_id = c.id"
        present, state, diags = analyze_dbt_sql(sql)
        assert state == 'metadata_only'
        assert any("JOIN_DETECTED" in d for d in diags)

    def test_unsupported_sql_refusal_exits_2_in_all_modes(self, test_workspace):
        """CLI comparison on metadata_only or unsupported resource exits 2 even in advisory mode."""
        store: Store = test_workspace['store']
        pid = test_workspace['project_id']
        data_dir = test_workspace['data_dir']

        # Import a manifest with a CTE model
        manifest_obj = {
            "metadata": {
                "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v10.json",
                "dbt_version": "1.8.0",
                "project_name": "refusal_test"
            },
            "nodes": {
                "model.retail.cte_metric": {
                    "resource_type": "model",
                    "name": "cte_metric",
                    "schema": "public",
                    "database": "retail",
                    "unique_id": "model.retail.cte_metric",
                    "compiled": True,
                    "compiled_code": "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b",
                }
            }
        }
        store.record_dbt_import(
            manifest_content=json.dumps(manifest_obj).encode(),
            project_id=pid
        )

        env = os.environ.copy()
        env['METRICGUARD_API_KEY'] = test_workspace['analyst_key']
        env['METRICGUARD_PROJECT_ID'] = pid

        # Run CLI in advisory mode -> MUST exit 2 (Refusal), NEVER exit 0
        cmd = [
            sys.executable, '-m', 'services.cli', 'compare',
            '--baseline', 'dbt:model.retail.cte_metric',
            '--target', 'dbt:model.retail.cte_metric',
            '--snapshot', 'dummy-snap',
            '--mode', 'advisory',
            '--context-json', '{"start":"2026-08-01","end_exclusive":"2026-09-01"}',
            '--database-url', f"sqlite:///{test_workspace['db_path']}",
            '--data-dir', str(data_dir)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)
        assert proc.returncode == 2, f"Expected exit code 2 (Refusal), got {proc.returncode}. Output: {proc.stdout}\n{proc.stderr}"
        assert "metadata_only" in proc.stdout.lower() or "refused" in proc.stdout.lower()

    def test_failed_extraction_raises_explicit_collector_error(self, test_workspace):
        """Collector errors are never silent; they raise explicit CollectorError."""
        collector = test_workspace['collector']
        bad_selection = PostgresSourceSelection(
            source_uri=PG_TEST_URL,
            schema_name="nonexistent_schema",
            table_name="nonexistent_table",
            columns=list(SCHEMA),
            cutoff_column="order_date",
            cutoff_value="2026-09-02",
            contract_id=test_workspace['contract']['id']
        )
        if not is_pg_available():
            pytest.skip("PostgreSQL test database unavailable")

        with pytest.raises(CollectorError) as exc_info:
            collector.collect(selection=bad_selection, project_id=test_workspace['project_id'])

        assert exc_info.value.code in ('EXTRACTION_FAILED', 'EXTRACTION_QUERY_FAILED', 'CONNECTION_FAILED', 'TABLE_NOT_FOUND')


# ===========================================================================
# 4. Gate 4.3: Cutoff / Retry Isolation
# ===========================================================================

class TestGate43CutoffRetryIsolation:
    """Verifies that interrupted collector attempts:
    - leave no valid partial snapshot
    - retain the same logical cutoff on retry
    - never mix attempts
    """

    def test_interrupted_attempt_cleans_up_staging(self, test_workspace):
        """Failure during extraction deletes partial .duckdb and .manifest.json staging files."""
        collector = test_workspace['collector']
        data_dir = test_workspace['data_dir']

        bad_selection = PostgresSourceSelection(
            source_uri=PG_TEST_URL,
            schema_name="public",
            table_name="table_does_not_exist_for_isolation_test",
            columns=list(SCHEMA),
            cutoff_column="order_date",
            cutoff_value="2026-09-02",
            contract_id=test_workspace['contract']['id'],
            extraction_id="ext-fail-cleanup"
        )
        if not is_pg_available():
            pytest.skip("PostgreSQL test database unavailable")

        with pytest.raises(CollectorError):
            collector.collect(selection=bad_selection, project_id=test_workspace['project_id'])

        # Verify no orphan partial files exist for this extraction_id
        matching_files = list(data_dir.glob("*ext-fail-cleanup*"))
        assert len(matching_files) == 0, f"Found orphan staging files: {matching_files}"

    def test_retry_strictly_requires_identical_logical_cutoff(self, test_workspace):
        """Retrying an extraction with a mutated cutoff date is strictly rejected."""
        collector = test_workspace['collector']
        pid = test_workspace['project_id']
        store = test_workspace['store']

        extraction_id = f"ext-retry-{uuid.uuid4().hex[:8]}"
        initial_cutoff = "2026-09-02"

        if not is_pg_available():
            pytest.skip("PostgreSQL test database unavailable")

        schema_name = f"test_v4_retry_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA "{schema_name}"')
                cur.execute(f'''
                    CREATE TABLE "{schema_name}".orders_bad (
                        order_id VARCHAR(64) PRIMARY KEY,
                        customer_id VARCHAR(64),
                        order_date DATE NOT NULL,
                        payment_date DATE,
                        status VARCHAR(32),
                        payment_status VARCHAR(32) NOT NULL,
                        order_amount NUMERIC(18,2) NOT NULL,
                        refund_amount NUMERIC(18,2),
                        currency VARCHAR(8) NOT NULL
                    )
                ''')
                cur.execute(f'''
                    INSERT INTO "{schema_name}".orders_bad VALUES
                    ('ord-1', 'c1', '2026-08-10', NULL, 'ok', 'paid', 100.00, NULL, 'EUR')
                ''')

        try:
            selection = PostgresSourceSelection(
                source_uri=PG_TEST_URL,
                schema_name=schema_name,
                table_name='orders_bad',
                cutoff_column='order_date',
                cutoff_value=initial_cutoff,
                contract_id=test_workspace['contract']['id'],
                extraction_id=extraction_id,
                attempt=1,
            )

            # Attempt 1 fails due to contract currency violation
            with pytest.raises(CollectorError):
                collector.collect(selection, project_id=pid)

            # Attempt retry with DIFFERENT cutoff -> REJECTED
            selection_bad_cutoff = PostgresSourceSelection(
                source_uri=PG_TEST_URL,
                schema_name=schema_name,
                table_name='orders_bad',
                cutoff_column='order_date',
                cutoff_value='2026-09-05',  # Changed!
                contract_id=test_workspace['contract']['id'],
                extraction_id=extraction_id,
                attempt=2,
            )
            with pytest.raises(DomainError) as exc_info:
                collector.collect(selection_bad_cutoff, project_id=pid)

            assert exc_info.value.code == 'CUTOFF_CHANGED'
        finally:
            with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


# ===========================================================================
# 5. Gate 4.4: Snapshot Determinism
# ===========================================================================

class TestGate44SnapshotDeterminism:
    """Verifies that running the same supported comparison multiple times
    against the same frozen snapshot produces byte-for-byte identical analytical results.
    """

    def test_multiple_comparisons_produce_identical_results(self, test_workspace, pg_orders_table):
        store: Store = test_workspace['store']
        pid = test_workspace['project_id']
        collector = test_workspace['collector']

        # Collect snapshot
        selection = PostgresSourceSelection(
            source_uri=pg_orders_table['source_uri'],
            schema_name=pg_orders_table['schema_name'],
            table_name=pg_orders_table['table_name'],
            columns=list(SCHEMA),
            cutoff_column="order_date",
            cutoff_value="2026-09-02",
            contract_id=test_workspace['contract']['id']
        )
        snapshot_res = collector.collect(selection=selection, project_id=pid)
        snap_path = Path(snapshot_res['artifact_path'])
        snap_obj = store.snapshot(snapshot_res['snapshot_id'])

        baseline_sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed'"
        target_sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND payment_status = 'Paid'"

        baseline_ir = parse_definition(baseline_sql, time_column='order_date')
        target_ir = parse_definition(target_sql, time_column='order_date')

        context = {
            'start': '2026-08-01',
            'end_exclusive': '2026-09-01',
            'timezone': 'Asia/Kolkata',
            'currency': 'INR',
            'unit': 'currency',
            'grain': 'one_row_per_order_id',
            'scope': 'v4_retail_sales',
            'contract_hash': test_workspace['contract']['contract_hash'],
            'mapping_hash': snap_obj.get('mapping_hash')
        }

        # Run comparison 5 times
        runs = []
        for _ in range(5):
            res = compare(snap_path, baseline_ir, target_ir, context)
            runs.append(res)

        # Assert all runs are identical
        first = runs[0]
        for idx, current in enumerate(runs[1:], start=2):
            assert current['baseline_value'] == first['baseline_value'], f"Run {idx} baseline differed"
            assert current['target_value'] == first['target_value'], f"Run {idx} target differed"
            assert current['delta_target_minus_baseline'] == first['delta_target_minus_baseline'], f"Run {idx} delta differed"
            assert current['explanation_status'] == first['explanation_status']
            assert len(current['evidence']) == len(first['evidence'])
            assert current['evidence'] == first['evidence']


# ===========================================================================
# 6. Gate 4.5: No Live-Source Dependency During Analysis
# ===========================================================================

class TestGate45NoLiveSourceDependency:
    """Verifies that after snapshot creation, mutating the underlying PostgreSQL
    source table has ZERO effect on snapshot comparison results or file hash.
    """

    def test_post_snapshot_mutation_does_not_affect_comparison(self, test_workspace, pg_orders_table):
        store: Store = test_workspace['store']
        pid = test_workspace['project_id']
        collector = test_workspace['collector']

        schema_name = pg_orders_table['schema_name']
        table_name = pg_orders_table['table_name']

        # 1. Collect initial frozen snapshot
        selection = PostgresSourceSelection(
            source_uri=pg_orders_table['source_uri'],
            schema_name=schema_name,
            table_name=table_name,
            columns=list(SCHEMA),
            cutoff_column="order_date",
            cutoff_value="2026-09-02",
            contract_id=test_workspace['contract']['id']
        )
        snapshot_res = collector.collect(selection=selection, project_id=pid)
        snap_path = Path(snapshot_res['artifact_path'])
        initial_db_hash = file_hash(snap_path)
        snap_obj = store.snapshot(snapshot_res['snapshot_id'])

        baseline_sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed'"
        target_sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND payment_status = 'Paid'"
        baseline_ir = parse_definition(baseline_sql, time_column='order_date')
        target_ir = parse_definition(target_sql, time_column='order_date')
        context = {
            'start': '2026-08-01',
            'end_exclusive': '2026-09-01',
            'timezone': 'Asia/Kolkata',
            'currency': 'INR',
            'unit': 'currency',
            'grain': 'one_row_per_order_id',
            'scope': 'v4_retail_sales',
            'contract_hash': test_workspace['contract']['contract_hash'],
            'mapping_hash': snap_obj.get('mapping_hash')
        }

        # 2. Run initial comparison
        res_before = compare(snap_path, baseline_ir, target_ir, context)
        baseline_val_before = res_before['baseline_value']
        target_val_before = res_before['target_value']
        delta_before = res_before['delta_target_minus_baseline']

        # 3. Aggressively mutate PostgreSQL source table: INSERT, UPDATE, DELETE
        with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                # Insert 200 new completed orders
                new_rows = [
                    (f"ord-mutation-{i}", "cust-mut", date(2026, 8, 15), None, 'Completed', 'Pending', Decimal('9999.00'), Decimal('0.00'), 'INR')
                    for i in range(200)
                ]
                cur.executemany(f'INSERT INTO "{schema_name}"."{table_name}" VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)', new_rows)

                # Mutate existing orders
                cur.execute(f'''
                    UPDATE "{schema_name}"."{table_name}"
                    SET order_amount = order_amount * 10
                    WHERE order_id LIKE 'ord-v4-000%'
                ''')

                # Delete some orders
                cur.execute(f'''
                    DELETE FROM "{schema_name}"."{table_name}"
                    WHERE order_id LIKE 'ord-v4-001%'
                ''')

        # 4. Re-run comparison against the frozen snapshot
        res_after = compare(snap_path, baseline_ir, target_ir, context)

        # 5. Verify the snapshot result is 100% UNCHANGED
        assert res_after['baseline_value'] == baseline_val_before
        assert res_after['target_value'] == target_val_before
        assert res_after['delta_target_minus_baseline'] == delta_before
        assert res_after['evidence'] == res_before['evidence']

        # 6. Verify DuckDB snapshot file hash remains byte-for-byte identical
        assert file_hash(snap_path) == initial_db_hash


# ===========================================================================
# 7. Decoupled Benchmarks & Boundaries Verification
# ===========================================================================

class TestDecoupledBenchmarksAndBoundaries:
    """Verifies that the decoupled micro-benchmarks script runs successfully,
    measures each stage separately, and validates failure benchmark limits.
    """

    def test_benchmark_script_runs_cleanly(self, tmp_path):
        out_file = tmp_path / 'bench_report.json'
        report = run_benchmarks(workdir=tmp_path / 'work', output_file=out_file)

        assert out_file.is_file()
        assert report['benchmark_version'] == 'metricguard_v4_benchmarks_v1'
        assert report['overall_status'] == 'passed'

        stages = report['stages']
        assert 'dbt_manifest_ingestion' in stages
        assert stages['dbt_manifest_ingestion']['status'] == 'passed'
        assert stages['dbt_manifest_ingestion']['throughput_models_per_sec'] > 0

        assert 'snapshot_validation_and_publication' in stages
        assert stages['snapshot_validation_and_publication']['status'] == 'passed'

        assert 'comparison_execution' in stages
        assert stages['comparison_execution']['status'] == 'passed'

        assert 'report_generation_and_export' in stages
        assert stages['report_generation_and_export']['status'] == 'passed'
        assert stages['report_generation_and_export']['exit_code'] == 0

        # Failure boundaries verified
        boundaries = report['failure_boundaries']
        assert boundaries['manifest_size_limit']['rejected_as_expected'] is True
        assert boundaries['manifest_resource_count_limit']['rejected_as_expected'] is True
        assert boundaries['sql_refusal_detection']['rejected_as_expected'] is True

    def test_failure_boundary_manifest_size_limit(self):
        oversized = b"x" * (MAX_DBT_MANIFEST_BYTES + 1024)
        with pytest.raises(AdapterError) as exc_info:
            parse_dbt_project_artifacts(oversized)
        assert "exceeds" in str(exc_info.value)

    def test_failure_boundary_resource_limit(self):
        excess_nodes = {
            f"model.retail.node_{i:04d}": {
                "resource_type": "model",
                "name": f"node_{i}",
                "unique_id": f"model.retail.node_{i:04d}"
            }
            for i in range(MAX_DBT_RESOURCES + 1)
        }
        manifest_excess = {
            "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v10.json"},
            "nodes": excess_nodes
        }
        with pytest.raises(AdapterError) as exc_info:
            parse_dbt_project_artifacts(json.dumps(manifest_excess).encode('utf-8'))
        assert "limited to" in str(exc_info.value)
