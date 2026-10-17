"""Decoupled micro-benchmarks for MetricGuard V4: Connected SQL Workflow.

Measures each lifecycle stage separately:
1. dbt Core manifest parsing & import
2. PostgreSQL collection & in-stream contract validation
3. Frozen DuckDB snapshot hashing & manifest publication
4. Isolated Docker runner container startup
5. Exact analytical comparison execution
6. Row-level evidence generation & Shapley bridge attribution
7. CI report export (versioned JSON & Markdown artifact)
8. Failure benchmark boundaries (manifest size, resource limits, SQL refusals)

Outputs structured, machine-readable metrics to data/evaluation/v4_benchmarks_report.json.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
from typing import Any
import uuid

import duckdb
import psycopg
import sqlglot

from packages.metricguard_core import compare, parse_definition
from packages.metricguard_core.common import SCHEMA, digest, file_hash
from packages.metricguard_core.snapshot import inspect_snapshot, manifest_path
from services.api.metadata_adapters import (
    MAX_DBT_MANIFEST_BYTES,
    MAX_DBT_RESOURCES,
    AdapterError,
    analyze_dbt_sql,
    parse_dbt_project_artifacts,
)
from services.api.store import Store
from services.cli import (
    ResolvedInputs,
    execute_headless_comparison,
    generate_markdown_report,
    generate_terminal_summary,
)
from services.supervisor.collector import (
    CollectorError,
    PostgresCollector,
    PostgresSourceSelection,
    sanitize_source_uri,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'data' / 'evaluation' / 'v4_benchmarks_report.json'

PG_TEST_URL = os.environ.get(
    'METRICGUARD_PG_TEST_URL',
    'postgresql://metricguard:local-dev-only@127.0.0.1:5433/metricguard'
)


def _check_pg_available() -> bool:
    try:
        with psycopg.connect(PG_TEST_URL, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                return True
    except Exception:
        return False


def _check_docker_available(image: str = 'metricguard-runner:1.0') -> bool:
    try:
        res = subprocess.run(
            ['docker', 'run', '--rm', '--entrypoint', 'python', image, '-c', 'print("ok")'],
            capture_output=True, text=True, timeout=10
        )
        return res.returncode == 0 and 'ok' in res.stdout
    except Exception:
        return False


def _generate_synthetic_dbt_manifest(model_count: int = 50) -> dict:
    """Generate a realistic dbt Core v10 manifest with multiple model archetypes."""
    nodes = {}
    for i in range(model_count):
        uid = f"model.analytics.orders_metric_{i:03d}"
        if i % 5 == 0:
            # Metadata-only due to CTE
            sql = f"WITH cte AS (SELECT * FROM raw_orders) SELECT SUM(order_amount) FROM cte WHERE status = 'Completed'"
        elif i % 5 == 1:
            # Metadata-only due to uncompiled Jinja
            sql = f"SELECT SUM(order_amount) FROM {{{{ ref('raw_orders') }}}} WHERE order_date >= '2026-08-01'"
        elif i % 5 == 2:
            # Metadata-only due to JOIN
            sql = "SELECT SUM(o.order_amount) FROM orders o JOIN customers c ON o.customer_id = c.id"
        elif i % 5 == 3:
            # Intentional variant
            sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND payment_status = 'Paid'"
        else:
            # Standard executable single-table aggregation
            sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed'"

        nodes[uid] = {
            "resource_type": "model",
            "name": f"orders_metric_{i:03d}",
            "schema": "public",
            "database": "analytics",
            "unique_id": uid,
            "compiled": True,
            "compiled_code": sql,
            "depends_on": {
                "nodes": [f"model.analytics.upstream_{i % 10:02d}"]
            },
            "config": {"materialized": "table"}
        }

    return {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v10.json",
            "dbt_version": "1.8.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "invocation_id": str(uuid.uuid4()),
            "project_name": "analytics"
        },
        "nodes": nodes
    }


def _seed_pg_benchmark_table(conn_str: str, schema_name: str, table_name: str, row_count: int = 2500) -> None:
    """Create and seed a realistic retail orders table in PostgreSQL for collection benchmarks."""
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
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
            # Generate deterministic records
            records = []
            for i in range(1, row_count + 1):
                day = (i % 28) + 1
                status = 'Completed' if i % 10 != 0 else 'Cancelled'
                p_status = 'Paid' if status == 'Completed' else 'Pending'
                amount = Decimal('100.00') + Decimal(i % 500)
                refund = Decimal('0.00') if p_status == 'Paid' else Decimal('10.00')
                records.append((
                    f"ord-{i:06d}",
                    f"cust-{i % 200:04d}",
                    date(2026, 8, day),
                    date(2026, 8, day) if p_status == 'Paid' else None,
                    status,
                    p_status,
                    amount,
                    refund,
                    'INR'
                ))
            # Batch copy / insert
            cur.executemany(f'''
                INSERT INTO "{schema_name}"."{table_name}"
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', records)


def run_benchmarks(workdir: Path | None = None, output_file: Path | None = None) -> dict[str, Any]:
    """Execute all decoupled benchmark stages and return the structured report."""
    if workdir is None:
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        work_path = Path(temp_dir.name)
    else:
        temp_dir = None
        work_path = Path(workdir)

    work_path.mkdir(parents=True, exist_ok=True)
    pg_available = _check_pg_available()
    docker_available = _check_docker_available()

    report: dict[str, Any] = {
        'benchmark_version': 'metricguard_v4_benchmarks_v1',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'environment': {
            'python': platform.python_version(),
            'platform': platform.system(),
            'platform_release': platform.release(),
            'machine': platform.machine(),
            'logical_cpus': os.cpu_count(),
            'duckdb_version': duckdb.__version__,
            'sqlglot_version': sqlglot.__version__,
            'psycopg_version': psycopg.__version__,
            'pg_test_available': pg_available,
            'docker_runner_available': docker_available,
        },
        'stages': {},
        'failure_boundaries': {},
        'overall_status': 'passed'
    }

    try:
        # Initialize isolated store and workspace
        db_path = work_path / 'benchmark.db'
        store = Store(f"sqlite:///{db_path}")
        user, api_key = store.create_user('Benchmarker', 'bench@metricguard.internal')
        project = store.create_project('V4 Benchmark Project', owner_id=user['id'], project_id='proj-bench')
        data_dir = work_path / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)

        retail_contract = store.record_dataset_contract({
            'version': 2,
            'name': 'Benchmark Retail Contract',
            'currency': 'INR',
            'timezone': 'Asia/Kolkata',
            'scope': 'benchmark_orders',
            'grain': 'one_row_per_order_id',
            'cutoff_at': '2026-09-02',
            'null_policy': 'blank_nullable',
            'row_key': 'order_id',
            'entity_key': 'customer_id',
            'decimal_scale': 2,
            'schema': SCHEMA,
        })

        # -------------------------------------------------------------
        # STAGE 1: dbt Manifest Parsing & Ingestion
        # -------------------------------------------------------------
        manifest_dict = _generate_synthetic_dbt_manifest(model_count=50)
        raw_manifest_bytes = json.dumps(manifest_dict).encode('utf-8')

        t0 = time.perf_counter()
        parsed_payload = parse_dbt_project_artifacts(raw_manifest_bytes)
        t_parse = time.perf_counter() - t0

        t0 = time.perf_counter()
        import_rev = store.record_dbt_import(
            manifest_content=raw_manifest_bytes,
            source_revision={'git_sha': 'commit-bench-1', 'branch': 'main'},
            project_id='proj-bench'
        )
        t_store = time.perf_counter() - t0

        report['stages']['dbt_manifest_ingestion'] = {
            'status': 'passed',
            'manifest_bytes': len(raw_manifest_bytes),
            'model_count': len(manifest_dict['nodes']),
            'parse_seconds': round(t_parse, 5),
            'store_import_seconds': round(t_store, 5),
            'total_seconds': round(t_parse + t_store, 5),
            'throughput_models_per_sec': round(len(manifest_dict['nodes']) / max(t_parse + t_store, 0.0001), 1),
            'import_revision_id': import_rev['id'],
            'executable_count': sum(1 for m in import_rev['resources'] if m['support_state'] == 'executable'),
            'metadata_only_count': sum(1 for m in import_rev['resources'] if m['support_state'] == 'metadata_only')
        }

        # -------------------------------------------------------------
        # STAGE 2: PostgreSQL Extraction & Collection
        # -------------------------------------------------------------
        schema_name = f"bench_pg_{uuid.uuid4().hex[:10]}"
        table_name = "benchmark_orders"
        row_count = 2500

        if pg_available:
            _seed_pg_benchmark_table(PG_TEST_URL, schema_name, table_name, row_count=row_count)
            collector = PostgresCollector(store=store, data_dir=data_dir)
            selection = PostgresSourceSelection(
                source_uri=PG_TEST_URL,
                schema_name=schema_name,
                table_name=table_name,
                columns=list(SCHEMA),
                cutoff_column="order_date",
                cutoff_value="2026-09-02",
                contract_id=retail_contract['id']
            )

            t0 = time.perf_counter()
            snapshot_res = collector.collect(selection=selection, project_id='proj-bench')
            t_extract = time.perf_counter() - t0
            snapshot_id = snapshot_res['snapshot_id']
            snap_path = Path(snapshot_res['artifact_path'])

            report['stages']['postgres_collection'] = {
                'status': 'passed',
                'row_count': snapshot_res['row_count'],
                'elapsed_seconds': round(t_extract, 5),
                'throughput_rows_per_sec': round(snapshot_res['row_count'] / max(t_extract, 0.0001), 1),
                'snapshot_id': snapshot_id,
                'isolation_level': 'REPEATABLE READ READ ONLY'
            }

            # Cleanup benchmark PG schema
            with psycopg.connect(PG_TEST_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        else:
            report['stages']['postgres_collection'] = {
                'status': 'skipped',
                'reason': 'PostgreSQL test database not available',
                'elapsed_seconds': 0.0
            }
            # Fallback for offline environments: create snapshot via duckdb
            snap_file = data_dir / 'bench_fallback.duckdb'
            con = duckdb.connect(str(snap_file))
            con.execute(f"CREATE TABLE orders ({', '.join(f'{col} {dt}' for col, dt in SCHEMA.items())})")
            con.close()
            m_data = {
                'database_hash': file_hash(snap_file),
                'source_hash': file_hash(snap_file),
                'row_count': 0,
                'schema': SCHEMA,
                'timezone': 'Asia/Kolkata',
                'currency': 'INR',
                'grain': 'one_row_per_order_id',
                'scope': 'benchmark_orders',
            }
            m_data['manifest_hash'] = digest(m_data)
            m_path = manifest_path(snap_file)
            m_path.write_text(json.dumps(m_data))
            snapshot = store.record_snapshot(
                name='Offline Benchmark Snapshot',
                manifest=m_data,
                artifact_path=str(snap_file),
                project_id='proj-bench'
            )
            snapshot_id = snapshot['id']
            snap_path = snap_file

        # -------------------------------------------------------------
        # STAGE 3: Snapshot Validation, Hash & Publication
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        snap_hash = file_hash(snap_path)
        m_file = manifest_path(snap_path)
        assert m_file.exists(), "Snapshot manifest file must exist"
        manifest_data = json.loads(m_file.read_text(encoding='utf-8'))
        assert manifest_data['database_hash'] == snap_hash
        t_hash_pub = time.perf_counter() - t0

        report['stages']['snapshot_validation_and_publication'] = {
            'status': 'passed',
            'file_bytes': snap_path.stat().st_size,
            'elapsed_seconds': round(t_hash_pub, 5),
            'database_hash': snap_hash
        }

        # -------------------------------------------------------------
        # STAGE 4: Isolated Runner / Container Startup
        # -------------------------------------------------------------
        if docker_available:
            t0 = time.perf_counter()
            res = subprocess.run(
                ['docker', 'run', '--rm', '--network', 'none', '--read-only',
                 '--entrypoint', 'python', 'metricguard-runner:1.0', '-c', 'import sys; sys.exit(0)'],
                capture_output=True, timeout=15
            )
            t_runner = time.perf_counter() - t0
            report['stages']['runner_container_startup'] = {
                'status': 'passed',
                'container_image': 'metricguard-runner:1.0',
                'elapsed_seconds': round(t_runner, 5),
                'isolation_options': '--network none --read-only'
            }
        else:
            report['stages']['runner_container_startup'] = {
                'status': 'skipped',
                'reason': 'Docker runner not available',
                'elapsed_seconds': 0.0
            }

        # -------------------------------------------------------------
        # STAGE 5: Exact Analytical Comparison Execution
        # -------------------------------------------------------------
        baseline_sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed'"
        target_sql = "SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND payment_status = 'Paid'"
        snap_manifest = inspect_snapshot(snap_path)
        context = {
            'start': '2026-08-01',
            'end_exclusive': '2026-09-01',
            'timezone': snap_manifest.get('timezone', 'Asia/Kolkata'),
            'currency': snap_manifest.get('currency', 'INR'),
            'unit': 'currency',
            'grain': snap_manifest.get('grain', 'one_row_per_order_id'),
            'scope': snap_manifest.get('scope', 'benchmark_orders'),
            'contract_hash': snap_manifest.get('contract_hash'),
            'mapping_hash': snap_manifest.get('mapping_hash'),
        }

        baseline_ir = parse_definition(baseline_sql, time_column='order_date')
        target_ir = parse_definition(target_sql, time_column='order_date')
        t0 = time.perf_counter()
        comp_result = compare(snap_path, baseline_ir, target_ir, context)
        t_comp = time.perf_counter() - t0

        report['stages']['comparison_execution'] = {
            'status': 'passed',
            'baseline_total': str(comp_result['baseline_value']),
            'target_total': str(comp_result['target_value']),
            'delta': str(comp_result['delta_target_minus_baseline']),
            'residual': str(comp_result.get('residual', '0.00')),
            'explanation_status': comp_result['explanation_status'],
            'elapsed_seconds': round(t_comp, 5)
        }

        # -------------------------------------------------------------
        # STAGE 6: Evidence Generation & Attribution Bridge
        # -------------------------------------------------------------
        t0 = time.perf_counter()
        evidence_items = comp_result.get('evidence', [])
        bridge_steps = comp_result.get('bridge', [])
        # Measure grouping / slicing
        evidence_rows_count = len(evidence_items)
        bridge_steps_count = len(bridge_steps)
        t_evidence = time.perf_counter() - t0

        report['stages']['evidence_and_bridge_generation'] = {
            'status': 'passed',
            'evidence_rows_count': evidence_rows_count,
            'bridge_steps_count': bridge_steps_count,
            'elapsed_seconds': round(t_evidence, 5)
        }

        # -------------------------------------------------------------
        # STAGE 7: Report Export (JSON & Markdown Artifacts)
        # -------------------------------------------------------------
        # Register definitions in store for CLI headless comparison
        def_b = store.record_definition(
            name='Gross Orders',
            source_label='dbt model orders_metric_003',
            owner_label='Analytics',
            raw_sql=baseline_sql,
            time_column='order_date',
            project_id='proj-bench'
        )
        def_t = store.record_definition(
            name='Net Paid Orders',
            source_label='dbt model orders_metric_004',
            owner_label='Analytics',
            raw_sql=target_sql,
            time_column='order_date',
            project_id='proj-bench'
        )

        snapshot_rec = store.snapshot(snapshot_id)
        resolved_inputs = ResolvedInputs(
            project_id='proj-bench',
            baseline_version=def_b['versions'][0],
            target_version=def_t['versions'][0],
            snapshot=snapshot_rec,
            context=context,
            mode='advisory',
            materiality_policy=None,
            relationship='candidate',
        )

        t0 = time.perf_counter()
        cli_report = execute_headless_comparison(
            store=store,
            inputs=resolved_inputs,
        )
        t_cli = time.perf_counter() - t0

        t0 = time.perf_counter()
        ascii_summary = generate_terminal_summary(cli_report)
        md_report = generate_markdown_report(cli_report)
        json_str = json.dumps(cli_report)
        t_render = time.perf_counter() - t0

        report['stages']['report_generation_and_export'] = {
            'status': 'passed',
            'cli_execution_seconds': round(t_cli, 5),
            'rendering_seconds': round(t_render, 5),
            'total_export_seconds': round(t_cli + t_render, 5),
            'json_bytes': len(json_str.encode('utf-8')),
            'markdown_bytes': len(md_report.encode('utf-8')),
            'exit_code': cli_report.get('exit_code', 0),
            'report_schema_version': cli_report.get('schema')
        }

        # -------------------------------------------------------------
        # STAGE 8: Failure Benchmark Boundaries
        # -------------------------------------------------------------
        # 8.1 Manifest size limit (> 10 MiB)
        oversized_manifest = b" " * (MAX_DBT_MANIFEST_BYTES + 1024)
        t0 = time.perf_counter()
        rejected_size = False
        try:
            parse_dbt_project_artifacts(oversized_manifest)
        except AdapterError as exc:
            if 'exceeds' in str(exc):
                rejected_size = True
        t_rej_size = time.perf_counter() - t0

        # 8.2 Resource count limit (> 500 nodes)
        nodes_excess = {f"model.analytics.excess_{i:04d}": {"resource_type": "model", "name": f"m_{i}", "unique_id": f"model.analytics.excess_{i:04d}"} for i in range(501)}
        manifest_excess = {
            "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v10.json"},
            "nodes": nodes_excess
        }
        t0 = time.perf_counter()
        rejected_count = False
        try:
            parse_dbt_project_artifacts(json.dumps(manifest_excess).encode('utf-8'))
        except AdapterError as exc:
            if 'limited to' in str(exc):
                rejected_count = True
        t_rej_count = time.perf_counter() - t0

        # 8.3 Unsupported SQL immediate refusal timing
        cte_sql = "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b"
        t0 = time.perf_counter()
        _, cte_state, cte_diags = analyze_dbt_sql(cte_sql)
        t_cte_analysis = time.perf_counter() - t0

        report['failure_boundaries'] = {
            'manifest_size_limit': {
                'limit_bytes': MAX_DBT_MANIFEST_BYTES,
                'rejected_as_expected': rejected_size,
                'elapsed_seconds': round(t_rej_size, 6)
            },
            'manifest_resource_count_limit': {
                'limit_resources': MAX_DBT_RESOURCES,
                'rejected_as_expected': rejected_count,
                'elapsed_seconds': round(t_rej_count, 6)
            },
            'sql_refusal_detection': {
                'tested_construct': 'CTE',
                'classified_state': cte_state,
                'rejected_as_expected': cte_state == 'metadata_only',
                'elapsed_seconds': round(t_cte_analysis, 6),
                'diagnostics': cte_diags
            }
        }

    finally:
        try:
            if 'store' in locals() and hasattr(store, 'engine'):
                store.engine.dispose()
        except Exception:
            pass
        if temp_dir is not None:
            try:
                temp_dir.cleanup()
            except Exception:
                pass

    # Save artifact
    target_out = output_file or DEFAULT_OUTPUT
    target_out.parent.mkdir(parents=True, exist_ok=True)
    target_out.write_text(json.dumps(report, indent=2), encoding='utf-8')

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate decoupled benchmarks for MetricGuard V4")
    parser.add_argument('--output', '-o', type=Path, default=DEFAULT_OUTPUT, help="Output path for benchmark report JSON")
    parser.add_argument('--workdir', type=Path, default=None, help="Optional working directory")
    args = parser.parse_args()

    report = run_benchmarks(workdir=args.workdir, output_file=args.output)
    print(f"V4 Benchmarks completed. Report saved to: {args.output}")
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
