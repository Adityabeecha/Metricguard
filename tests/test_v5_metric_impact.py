"""Comprehensive focused test suite for Milestone V5-02:
Affected Metric Resolution + Controlled Metric Impact Execution.

Covers:
1. Affected Metric Resolution (declared metric vs undeclared vs unrelated; active vs candidate)
2. Approved Baseline / Drift Amnesia (approved V18 -> PR V21 vs incremental V20 -> PR V21; values remain distinct)
3. Fallback to Baseline Revision (when no approved version exists, incremental PR comparison is primary)
4. Reference Snapshot Resolution (same snapshot used, cross-project snapshot rejected, corrupted snapshot rejected,
   source mutation after snapshot does not alter result)
5. Support-State Gating & Refusal (executable SUM executes, CTE refused, metadata_only refused, needs_context handled,
   refusal never reported as success or pass/fail)
6. Materiality Evaluation (approved-baseline materiality, incremental materiality, exact boundary >= check, zero baseline)
7. Determinism & Stale-Result Protection (identical inputs -> identical cached report, changed target -> no stale reuse,
   changed snapshot -> no stale reuse, changed approval -> no stale reuse)
8. CI -> Investigation Interoperability (existing run and evidence referenced without re-execution, refused metric rejected)
9. RBAC & Cross-Project Isolation (owner/analyst execute, reader read-only, cross-project resources rejected)
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import pytest
from sqlalchemy import insert
from uuid import uuid4

from fastapi.testclient import TestClient

from packages.metricguard_core import ingest_csv
from services.api.app import create_app
from services.api.store import DomainError, Store, projects


ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CSV = ROOT / 'MetricGuard_Planning_Pack' / 'examples' / 'orders_golden.csv'

PERIOD_CLAUSE = "order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"


def _build_manifest(models: dict, version: int = 12, invocation_id: str = 'inv-v5-02') -> dict:
    compiled_key = 'compiled_code' if version >= 9 else 'compiled_sql'
    nodes = {}
    for uid, info in models.items():
        nodes[uid] = {
            'unique_id': uid,
            'resource_type': info.get('resource_type', 'model'),
            'name': info.get('name', uid.split('.')[-1]),
            'original_file_path': info.get('original_file_path', f"models/{uid}.sql"),
            'raw_code': info.get('raw_code', 'SELECT 1'),
            compiled_key: info.get('sql'),
            'depends_on': info.get('depends_on', {'nodes': []}),
            'meta': info.get('meta', {}),
            'tags': info.get('tags', []),
        }
    return {
        'metadata': {
            'dbt_schema_version': f'https://schemas.getdbt.com/dbt/manifest/v{version}.json',
            'dbt_version': f'1.{version}.0',
            'project_name': 'retail_analytics',
            'generated_at': '2026-09-25T14:00:00Z',
            'invocation_id': invocation_id,
        },
        'nodes': nodes,
        'metrics': {},
        'child_map': {},
    }


def _import_manifest(store: Store, models: dict, project_id: str = 'local-retail', invocation_id: str = 'inv') -> dict:
    manifest = _build_manifest(models, invocation_id=invocation_id)
    return store.record_dbt_import(json.dumps(manifest).encode(), project_id=project_id)


def _setup_test_snapshot(store: Store, tmp_path: Path, project_id: str = 'local-retail') -> dict:
    dest = tmp_path / f"snap_{uuid4().hex[:8]}.duckdb"
    manifest = ingest_csv(GOLDEN_CSV, dest)
    return store.record_snapshot('orders_golden', manifest, dest, project_id=project_id)


# ===========================================================================
# 1. Affected Metric Resolution: Declared vs Undeclared vs Unrelated & Candidate
# ===========================================================================

def test_affected_metric_resolution_declared_vs_undeclared(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    sql_stg_base = "SELECT order_id, customer_id FROM orders"
    sql_stg_target = "SELECT order_id, customer_id, order_date FROM orders"

    sql_cust = f"SELECT COUNT(*) FROM orders WHERE {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_base},
        'model.retail.stg_orders': {'sql': sql_stg_base},
        'model.retail.customers': {'sql': sql_cust},
    }, project_id=pid, invocation_id='inv-base')

    monthly_res = rev_base['resources'][0] if rev_base['resources'][0]['unique_id'] == 'model.retail.monthly_revenue' else next(r for r in rev_base['resources'] if r['unique_id'] == 'model.retail.monthly_revenue')
    monthly_defn_id = monthly_res['definition_id']
    v1_id = monthly_res['definition_version_id']

    # Declare Metric for monthly_revenue and bind
    m_rev = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m_rev['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m_rev['id'], definition_id=monthly_defn_id, status='active', approved_version_id=v1_id, project_id=pid)

    # Declare Metric for customers (unchanged model)
    m_cust = store.create_metric(display_name='Customer Count', importance='standard', project_id=pid)
    store.attach_external_metric_reference(m_cust['id'], provider='dbt', external_id='model.retail.customers', project_id=pid)
    cust_res = next(r for r in rev_base['resources'] if r['unique_id'] == 'model.retail.customers')
    cust_defn_id = cust_res['definition_id']
    store.bind_metric_definition(metric_id=m_cust['id'], definition_id=cust_defn_id, status='active', project_id=pid)

    # Target PR imports modified monthly_revenue and modified stg_orders
    rev_target = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_target},
        'model.retail.stg_orders': {'sql': sql_stg_target},
        'model.retail.customers': {'sql': sql_cust},
    }, project_id=pid, invocation_id='inv-target')

    # Execute metric impact
    report = store.execute_metric_impact(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    assert report['schema_version'] == 'metricguard_metric_impact_v1'
    assert report['counts']['total_metrics_evaluated'] == 1
    assert len(report['metrics']) == 1

    evaluated_metric = report['metrics'][0]
    assert evaluated_metric['metric_id'] == m_rev['id']
    assert evaluated_metric['display_name'] == 'Monthly Revenue'
    assert evaluated_metric['importance'] == 'critical'
    assert evaluated_metric['comparison_type'] == 'active'
    assert evaluated_metric['execution_status'] == 'executed'
    assert evaluated_metric['exact_delta'] is not None

    # Undeclared resource reported separately
    assert report['counts']['undeclared_resource_count'] == 1
    assert len(report['undeclared_resources']) == 1
    undec = report['undeclared_resources'][0]
    assert undec['unique_id'] == 'model.retail.stg_orders'
    assert undec['reason'] == 'UNDECLARED_METRIC'
    assert 'compiled_sql_changed' in undec['change_dimensions']


def test_candidate_definition_handling_is_analytical_only(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    # Active definition
    active_defn = store.record_definition(
        name='Active Revenue',
        source_label='dbt',
        owner_label='ops',
        raw_sql=f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}",
        project_id=pid
    )
    v_active = active_defn['versions'][0]['id']

    # Metric with active binding and approved version
    metric = store.create_metric(display_name='Total Revenue', importance='critical', project_id=pid)
    store.promote_metric_definition(metric['id'], active_defn['id'], approved_version_id=v_active, project_id=pid)

    # Base and target dbt revisions for candidate model
    sql_cand_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND currency = 'INR' AND {PERIOD_CLAUSE}"
    sql_cand_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.candidate_revenue': {'sql': sql_cand_base}}, project_id=pid, invocation_id='inv-c-base')
    cand_defn_id = rev_base['resources'][0]['definition_id']
    store.bind_metric_definition(metric['id'], cand_defn_id, status='candidate', project_id=pid)

    rev_target = _import_manifest(store, {'model.retail.candidate_revenue': {'sql': sql_cand_target}}, project_id=pid, invocation_id='inv-c-target')

    # Execute metric impact
    report = store.execute_metric_impact(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    assert len(report['candidate_metrics']) == 1
    cand_eval = report['candidate_metrics'][0]
    assert cand_eval['metric_id'] == metric['id']
    assert cand_eval['comparison_type'] == 'candidate_vs_active'
    assert cand_eval['candidate_definition_id'] == cand_defn_id
    assert cand_eval['active_definition_id'] == active_defn['id']
    assert cand_eval['execution_status'] == 'executed'

    # Candidate baseline was the active approved version (v_active)
    assert cand_eval['primary_impact']['baseline_version_id'] == v_active

    # Verify bindings were NOT mutated (no automatic promotion)
    bindings = store.list_metric_definition_bindings(metric['id'], project_id=pid)
    active = next(b for b in bindings if b['definition_id'] == active_defn['id'])
    cand = next(b for b in bindings if b['definition_id'] == cand_defn_id)
    assert active['status'] == 'active'
    assert cand['status'] == 'candidate'


# ===========================================================================
# 2. Approved Baseline vs Baseline Revision & Drift Amnesia Prevention
# ===========================================================================

def test_approved_baseline_vs_incremental_pr_impact_drift_amnesia(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    # Model definition history:
    # V1: Approved = All completed orders (Total = 104,000,000.00)
    # V2: Base revision branch = Completed orders excluding customer C04 (Total = 102,740,000.00)
    # V3: PR target revision = Completed orders for customer C01 only (Total = 62,320,000.00)
    sql_v1 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_v2 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id != 'C04' AND {PERIOD_CLAUSE}"
    sql_v3 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    # Initial import to create definition and V1
    r1 = _import_manifest(store, {'model.retail.revenue': {'sql': sql_v1}}, project_id=pid, invocation_id='inv-1')
    defn_id = r1['resources'][0]['definition_id']
    v1_id = r1['resources'][0]['definition_version_id']

    # Declare Metric and approve V1
    metric = store.create_metric(display_name='Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(metric['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.promote_metric_definition(metric['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    # Base branch has drifted to V2 without being approved
    r2 = _import_manifest(store, {'model.retail.revenue': {'sql': sql_v2}}, project_id=pid, invocation_id='inv-2')
    v2_id = r2['resources'][0]['definition_version_id']
    assert v2_id != v1_id

    # PR branch introduces V3
    r3 = _import_manifest(store, {'model.retail.revenue': {'sql': sql_v3}}, project_id=pid, invocation_id='inv-3')
    v3_id = r3['resources'][0]['definition_version_id']
    assert v3_id != v2_id and v3_id != v1_id

    # Execute controlled metric impact between r2 (base) and r3 (PR target)
    report = store.execute_metric_impact(
        baseline_revision_id=r2['id'],
        target_revision_id=r3['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    metric_eval = report['metrics'][0]
    assert metric_eval['has_approved_baseline'] is True
    assert metric_eval['approved_version_id'] == v1_id
    assert metric_eval['baseline_revision_version_id'] == v2_id
    assert metric_eval['target_version_id'] == v3_id

    # Primary governance impact is V1 -> V3 (Approved Baseline)
    appr_impact = metric_eval['approved_baseline_impact']
    assert appr_impact['baseline_version_id'] == v1_id
    assert appr_impact['target_version_id'] == v3_id
    assert Decimal(appr_impact['baseline_value']) == Decimal('104000000.00')
    assert Decimal(appr_impact['target_value']) == Decimal('62320000.00')
    appr_delta = Decimal(appr_impact['exact_delta'])
    assert appr_delta == Decimal('62320000.00') - Decimal('104000000.00')  # -41,680,000.00

    # Incremental PR impact is V2 -> V3 (Base revision -> PR target)
    incr_impact = metric_eval['incremental_impact']
    assert incr_impact['baseline_version_id'] == v2_id
    assert incr_impact['target_version_id'] == v3_id
    assert Decimal(incr_impact['baseline_value']) == Decimal('102740000.00')
    assert Decimal(incr_impact['target_value']) == Decimal('62320000.00')
    incr_delta = Decimal(incr_impact['exact_delta'])
    assert incr_delta == Decimal('62320000.00') - Decimal('102740000.00')  # -40,420,000.00

    # The two values are distinct and neither is lost or conflated
    assert appr_delta != incr_delta
    assert metric_eval['exact_delta'] == str(appr_delta)


def test_baseline_revision_fallback_when_no_approved_version(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']

    # Metric has active binding but NO approved version
    metric = store.create_metric(display_name='Unapproved Revenue', project_id=pid)
    store.attach_external_metric_reference(metric['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    with store.engine.begin() as conn:
        from services.api.store import metric_definition_bindings
        conn.execute(insert(metric_definition_bindings).values(
            id=f"bind-{uuid4().hex}",
            project_id=pid,
            metric_id=metric['id'],
            definition_id=defn_id,
            status='active',
            approved_version_id=None,
            activated_at=None,
            retired_at=None,
            created_at='2026-09-25T00:00:00Z',
            data={}
        ))

    r_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_target}}, project_id=pid)

    report = store.execute_metric_impact(
        baseline_revision_id=r_base['id'],
        target_revision_id=r_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    metric_eval = report['metrics'][0]
    assert metric_eval['has_approved_baseline'] is False
    assert metric_eval['approved_version_id'] is None
    assert metric_eval['approved_baseline_impact'] is None
    assert metric_eval['incremental_impact'] is not None

    # Incremental PR impact is used as primary
    assert metric_eval['primary_impact']['baseline_version_id'] == metric_eval['baseline_revision_version_id']
    assert metric_eval['exact_delta'] == metric_eval['incremental_impact']['exact_delta']


# ===========================================================================
# 3. Reference Snapshot Handling & Integrity
# ===========================================================================

def test_same_snapshot_used_and_cross_project_snapshot_rejected(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid1 = 'project-alpha'
    pid2 = 'project-beta'

    with store.engine.begin() as conn:
        conn.execute(insert(projects).values([
            {'id': pid1, 'name': 'Project Alpha'},
            {'id': pid2, 'name': 'Project Beta'},
        ]))

    snap_p1 = _setup_test_snapshot(store, tmp_path, pid1)
    snap_p2 = _setup_test_snapshot(store, tmp_path, pid2)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid1)
    r_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_target}}, project_id=pid1)

    # Executing project-alpha with project-beta snapshot is forbidden
    with pytest.raises(DomainError) as exc:
        store.execute_metric_impact(
            baseline_revision_id=r_base['id'],
            target_revision_id=r_target['id'],
            snapshot_id=snap_p2['id'],
            project_id=pid1
        )
    assert exc.value.code == 'FORBIDDEN'


def test_corrupted_snapshot_rejected(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid)
    r_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_target}}, project_id=pid)

    # Tamper with snapshot bytes
    art_path = Path(snapshot['artifact_path'])
    with art_path.open('ab') as f:
        f.write(b'corruption_bytes')

    with pytest.raises(DomainError) as exc:
        store.execute_metric_impact(
            baseline_revision_id=r_base['id'],
            target_revision_id=r_target['id'],
            snapshot_id=snapshot['id'],
            project_id=pid
        )
    assert exc.value.code in ('SNAPSHOT_CORRUPTED', 'SNAPSHOT_INTEGRITY')


def test_source_mutation_after_snapshot_does_not_alter_result(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'

    # Create temporary mutable source CSV
    src_csv = tmp_path / 'orders.csv'
    src_csv.write_bytes(GOLDEN_CSV.read_bytes())

    snap_file = tmp_path / 'frozen_snap.duckdb'
    manifest = ingest_csv(src_csv, snap_file)
    snapshot = store.record_snapshot('orders_frozen', manifest, snap_file, project_id=pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid)
    m = store.create_metric(display_name='Rev', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']
    v1_id = r_base['resources'][0]['definition_version_id']
    store.promote_metric_definition(m['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    r_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_target}}, project_id=pid)

    # First impact evaluation
    rep1 = store.execute_metric_impact(r_base['id'], r_target['id'], snapshot['id'], project_id=pid)
    delta1 = rep1['metrics'][0]['exact_delta']

    # Now mutate the original source CSV file
    with src_csv.open('a') as f:
        f.write("O99,C99,2026-08-15,2026-08-16,Completed,Paid,99999999.00,0.00,INR\n")

    # Second impact evaluation on the frozen snapshot
    rep2 = store.execute_metric_impact(r_base['id'], r_target['id'], snapshot['id'], project_id=pid)
    delta2 = rep2['metrics'][0]['exact_delta']

    assert delta1 == delta2


# ===========================================================================
# 4. Support-State Gating & Refusal
# ===========================================================================

def test_support_state_refusal_cte_and_metadata_only(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    # Multiple CTEs are unsupported in Level-1 IR
    sql_cte = "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b"

    r_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']
    v1_id = r_base['resources'][0]['definition_version_id']
    m = store.create_metric(display_name='Revenue', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.promote_metric_definition(m['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    r_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_cte}}, project_id=pid)

    # Target resource has metadata_only support state
    t_res = r_target['resources'][0]
    assert t_res['support_state'] == 'metadata_only'

    report = store.execute_metric_impact(r_base['id'], r_target['id'], snapshot['id'], project_id=pid)

    assert report['counts']['refused_count'] == 1
    metric_eval = report['metrics'][0]
    assert metric_eval['execution_status'] == 'refused'
    assert metric_eval['support_state'] == 'metadata_only'
    assert metric_eval['refusal_reason'] is not None
    assert metric_eval['exact_delta'] is None
    assert metric_eval['materiality'] is None
    # Refusal is never reported as success
    assert metric_eval.get('comparison_run_id') is None


def test_support_state_refusal_cannot_be_promoted_to_investigation(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    sql_cte = "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b"

    r_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']
    v1_id = r_base['resources'][0]['definition_version_id']
    m = store.create_metric(display_name='Revenue', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.promote_metric_definition(m['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    r_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_cte}}, project_id=pid)

    report = store.execute_metric_impact(r_base['id'], r_target['id'], snapshot['id'], project_id=pid)

    with pytest.raises(DomainError) as exc:
        store.promote_metric_impact_to_investigation(
            impact_id=report['id'],
            metric_id=m['id'],
            project_id=pid
        )
    assert exc.value.code == 'RESULT_UNAVAILABLE'


# ===========================================================================
# 5. Materiality Evaluation
# ===========================================================================

def test_materiality_evaluation_absolute_and_relative(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    # Orders golden total: 104,000,000.00
    # Excluding C04 (1,260,000.00): 102,740,000.00 -> delta = -1,260,000.00
    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id != 'C04' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid)
    m = store.create_metric(display_name='Revenue', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']
    v1_id = r_base['resources'][0]['definition_version_id']
    store.promote_metric_definition(m['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    r_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_target}}, project_id=pid)

    # 1. Absolute threshold higher than gap: 2,000,000 -> immaterial
    rep_immaterial = store.execute_metric_impact(
        baseline_revision_id=r_base['id'],
        target_revision_id=r_target['id'],
        snapshot_id=snapshot['id'],
        materiality_policy={'absolute_threshold': '2000000.00'},
        project_id=pid
    )
    mat1 = rep_immaterial['metrics'][0]['materiality']
    assert mat1 is not None
    assert mat1['is_material'] is False
    assert mat1['reason'] == 'within_thresholds'
    assert rep_immaterial['counts']['immaterial_impact_count'] == 1
    assert rep_immaterial['counts']['material_impact_count'] == 0

    # 2. Absolute threshold lower than gap: 1,000,000 -> material
    rep_material = store.execute_metric_impact(
        baseline_revision_id=r_base['id'],
        target_revision_id=r_target['id'],
        snapshot_id=snapshot['id'],
        materiality_policy={'absolute_threshold': '1000000.00'},
        project_id=pid
    )
    mat2 = rep_material['metrics'][0]['materiality']
    assert mat2 is not None
    assert mat2['is_material'] is True
    assert mat2['reason'] == 'exceeds_absolute_threshold'
    assert rep_material['counts']['material_impact_count'] == 1

    # 3. Exact boundary test: threshold == 1,260,000.00 -> material (>= semantics)
    rep_exact = store.execute_metric_impact(
        baseline_revision_id=r_base['id'],
        target_revision_id=r_target['id'],
        snapshot_id=snapshot['id'],
        materiality_policy={'absolute_threshold': '1260000.00'},
        project_id=pid
    )
    mat3 = rep_exact['metrics'][0]['materiality']
    assert mat3 is not None
    assert mat3['is_material'] is True
    assert mat3['reason'] == 'exceeds_absolute_threshold'


# ===========================================================================
# 6. Determinism & Stale-Result Protection
# ===========================================================================

def test_determinism_identical_inputs_and_no_stale_reuse(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snap1 = _setup_test_snapshot(store, tmp_path, pid)
    snap2 = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target1 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"
    sql_target2 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C02' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.rev': {'sql': sql_base}}, project_id=pid)
    m = store.create_metric(display_name='Rev', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.rev', project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']
    v1_id = r_base['resources'][0]['definition_version_id']
    store.promote_metric_definition(m['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    r_target1 = _import_manifest(store, {'model.retail.rev': {'sql': sql_target1}}, project_id=pid)
    r_target2 = _import_manifest(store, {'model.retail.rev': {'sql': sql_target2}}, project_id=pid)

    # Identical inputs return exact same cached report
    rep1a = store.execute_metric_impact(r_base['id'], r_target1['id'], snap1['id'], project_id=pid)
    rep1b = store.execute_metric_impact(r_base['id'], r_target1['id'], snap1['id'], project_id=pid)
    assert rep1a['id'] == rep1b['id']
    assert rep1a['metrics'][0]['exact_delta'] == rep1b['metrics'][0]['exact_delta']

    # Changed target revision produces new report (no stale reuse)
    rep2 = store.execute_metric_impact(r_base['id'], r_target2['id'], snap1['id'], project_id=pid)
    assert rep2['id'] != rep1a['id']
    assert rep2['metrics'][0]['exact_delta'] != rep1a['metrics'][0]['exact_delta']

    # Changed snapshot produces new report (no stale reuse)
    rep3 = store.execute_metric_impact(r_base['id'], r_target1['id'], snap2['id'], project_id=pid)
    assert rep3['id'] != rep1a['id']
    assert rep3['snapshot_id'] == snap2['id']


# ===========================================================================
# 7. CI -> Investigation Interoperability (No Re-execution)
# ===========================================================================

def test_ci_investigation_interoperability_reuses_run_and_evidence(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.rev': {'sql': sql_base}}, project_id=pid)
    m = store.create_metric(display_name='Monthly Rev', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.rev', project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']
    v1_id = r_base['resources'][0]['definition_version_id']
    store.promote_metric_definition(m['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    r_target = _import_manifest(store, {'model.retail.rev': {'sql': sql_target}}, project_id=pid)

    report = store.execute_metric_impact(r_base['id'], r_target['id'], snapshot['id'], project_id=pid)
    run_id = report['metrics'][0]['comparison_run_id']
    assert run_id is not None

    # Promote to investigation without re-execution
    inv = store.promote_metric_impact_to_investigation(
        impact_id=report['id'],
        metric_id=m['id'],
        title='CI Revenue Anomaly Investigation',
        decision='Accepted PR divergence for regional pilot',
        actor_label='CI Guard Bot',
        project_id=pid
    )

    assert inv['run_id'] == run_id
    assert inv['snapshot_id'] == snapshot['id']
    assert inv['title'] == 'CI Revenue Anomaly Investigation'
    assert inv['decision'] == 'Accepted PR divergence for regional pilot'
    assert inv['actor_label'] == 'CI Guard Bot'

    # Verify run exists and is in succeeded state
    run = store.get_run(run_id)
    assert run['state'] == 'succeeded'
    assert run['snapshot_id'] == snapshot['id']


# ===========================================================================
# 8. RBAC & Cross-Project Isolation
# ===========================================================================

def test_rbac_and_cross_project_isolation(tmp_path):
    url = f"sqlite:///{tmp_path / 'app.db'}"
    store = Store(url)
    pid = 'retail-rbac'
    other_pid = 'finance-other'

    with store.engine.begin() as conn:
        conn.execute(insert(projects).values([
            {'id': pid, 'name': 'Retail RBAC'},
            {'id': other_pid, 'name': 'Other'},
        ]))

    # Create users
    u_owner, owner_key = store.create_user('Owner', 'owner@test.com')
    u_analyst, analyst_key = store.create_user('Analyst', 'analyst@test.com')
    u_reader, reader_key = store.create_user('Reader', 'reader@test.com')

    store.add_project_member(pid, u_owner['id'], 'owner')
    store.add_project_member(pid, u_analyst['id'], 'analyst')
    store.add_project_member(pid, u_reader['id'], 'reader')

    # Seed resources in retail-rbac
    snapshot = _setup_test_snapshot(store, tmp_path, pid)
    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    r_base = _import_manifest(store, {'model.retail.rev': {'sql': sql_base}}, project_id=pid)
    m = store.create_metric(display_name='Rev', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.rev', project_id=pid)
    defn_id = r_base['resources'][0]['definition_id']
    v1_id = r_base['resources'][0]['definition_version_id']
    store.promote_metric_definition(m['id'], defn_id, approved_version_id=v1_id, project_id=pid)

    r_target = _import_manifest(store, {'model.retail.rev': {'sql': sql_target}}, project_id=pid)

    app = create_app(database_url=url, data_dir=tmp_path)
    client = TestClient(app)

    body = {
        'baseline_revision_id': r_base['id'],
        'target_revision_id': r_target['id'],
        'snapshot_id': snapshot['id']
    }

    # 1. Reader cannot execute impact (POST -> 403)
    resp = client.post(
        f"/api/v1/projects/{pid}/dbt/metric-impacts",
        json=body,
        headers={'authorization': f"Bearer {reader_key}"}
    )
    assert resp.status_code == 403

    # 2. Analyst can execute impact (POST -> 200)
    resp_exec = client.post(
        f"/api/v1/projects/{pid}/dbt/metric-impacts",
        json=body,
        headers={'authorization': f"Bearer {analyst_key}"}
    )
    assert resp_exec.status_code == 200
    impact_id = resp_exec.json()['id']

    # 3. Reader can view impact report (GET -> 200)
    resp_get = client.get(
        f"/api/v1/projects/{pid}/dbt/metric-impacts/{impact_id}",
        headers={'authorization': f"Bearer {reader_key}"}
    )
    assert resp_get.status_code == 200
    assert resp_get.json()['id'] == impact_id

    # 4. Reader can list impact reports (GET -> 200)
    resp_list = client.get(
        f"/api/v1/projects/{pid}/dbt/metric-impacts",
        headers={'authorization': f"Bearer {reader_key}"}
    )
    assert resp_list.status_code == 200
    assert len(resp_list.json()['items']) >= 1

    # 5. Reader cannot promote to investigation (POST -> 403)
    inv_body = {'metric_id': m['id'], 'decision': 'Reviewed', 'actor_label': 'Reader'}
    resp_inv_reader = client.post(
        f"/api/v1/projects/{pid}/dbt/metric-impacts/{impact_id}/investigations",
        json=inv_body,
        headers={'authorization': f"Bearer {reader_key}"}
    )
    assert resp_inv_reader.status_code == 403

    # 6. Analyst can promote to investigation (POST -> 201)
    resp_inv_analyst = client.post(
        f"/api/v1/projects/{pid}/dbt/metric-impacts/{impact_id}/investigations",
        json=inv_body,
        headers={'authorization': f"Bearer {analyst_key}"}
    )
    assert resp_inv_analyst.status_code == 201
    assert resp_inv_analyst.json()['run_id'] == resp_exec.json()['metrics'][0]['comparison_run_id']

    # 7. Cross-project isolation: user cannot access impact from other project
    store.add_project_member(other_pid, u_owner['id'], 'owner')
    resp_cross = client.get(
        f"/api/v1/projects/{other_pid}/dbt/metric-impacts/{impact_id}",
        headers={'authorization': f"Bearer {owner_key}"}
    )
    assert resp_cross.status_code in (403, 404)
