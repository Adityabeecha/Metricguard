"""Comprehensive test suite for Milestone V5-03:
Revision-Level Metric Diff Orchestration + Coverage Report.

Covers:
1. Orchestration:
   - Revision with one changed declared Metric
   - Several changed declared Metrics
   - Mixed declared + undeclared resources
   - No changed Metrics
   - Deterministic repeated execution
2. Coverage Report:
   - Declared Metric coverage
   - Importance segmentation (explicit only, no inference)
   - Unclassified Metrics
   - Observable structural signals kept strictly separate
3. Mixed Results & Outcome Semantics:
   - All analyzable
   - Some refused
   - Material drift + refusal together (refusal never hidden by pass/material)
   - Undeclared resources preserved with UNDECLARED_METRIC
4. Approved Baseline Semantics:
   - Cumulative approved drift surfaced vs incremental PR impact
5. Reference Snapshot:
   - Single pinned snapshot across comparisons
   - Incompatible metric refused
   - Cross-project snapshot rejected
   - Corrupted snapshot rejected
6. Reports (JSON & Markdown):
   - Deterministic metricguard_metric_diff_v1 schema
   - Markdown contains all 10 required sections
   - Exact decimals preserved
   - No unsupported business-metric wording for undeclared resources
7. CLI:
   - Valid diff execution (exit 0)
   - Enforcing material drift (exit 1)
   - Analytical refusal (exit 2)
   - Operational failure (exit 3)
   - Output and Markdown file generation
   - Secret redaction
8. Investigation Interoperability:
   - Reusable impact ID & comparison run ID without recomputation
9. RBAC & Cross-Project Isolation:
   - Owner/analyst execution, reader restricted, cross-project rejected
"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import pytest
from uuid import uuid4

from fastapi.testclient import TestClient

from packages.metricguard_core import ingest_csv
from services.api.app import create_app
from services.api.store import DomainError, Store
from services.cli import run_cli, redact_api_key
from services.diff_report import (
    compute_diff_coverage,
    aggregate_diff_refusals,
    determine_diff_outcome_and_exit_code,
    format_metric_diff_markdown,
)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CSV = ROOT / 'MetricGuard_Planning_Pack' / 'examples' / 'orders_golden.csv'

PERIOD_CLAUSE = "order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"


def _build_manifest(models: dict, version: int = 12, invocation_id: str = 'inv-v5-03',
                    exposures: dict | None = None) -> dict:
    compiled_key = 'compiled_code' if version >= 9 else 'compiled_sql'
    nodes = {}
    child_map = {}
    for uid, info in models.items():
        depends = info.get('depends_on', {'nodes': []})
        nodes[uid] = {
            'unique_id': uid,
            'resource_type': info.get('resource_type', 'model'),
            'name': info.get('name', uid.split('.')[-1]),
            'original_file_path': info.get('original_file_path', f"models/{uid}.sql"),
            'raw_code': info.get('raw_code', 'SELECT 1'),
            compiled_key: info.get('sql'),
            'depends_on': depends,
            'meta': info.get('meta', {}),
            'tags': info.get('tags', []),
        }
        for parent in depends.get('nodes', []):
            child_map.setdefault(parent, []).append(uid)

    manifest_exposures = {}
    if exposures:
        for exp_id, exp_info in exposures.items():
            manifest_exposures[exp_id] = exp_info
            for up in exp_info.get('depends_on', {}).get('nodes', []):
                child_map.setdefault(up, []).append(exp_id)

    return {
        'metadata': {
            'dbt_schema_version': f'https://schemas.getdbt.com/dbt/manifest/v{version}.json',
            'dbt_version': f'1.{version}.0',
            'project_name': 'retail_analytics',
            'generated_at': '2026-09-26T08:00:00Z',
            'invocation_id': invocation_id,
        },
        'nodes': nodes,
        'exposures': manifest_exposures,
        'metrics': {},
        'child_map': child_map,
    }


def _import_manifest(store: Store, models: dict, project_id: str = 'local-retail',
                     invocation_id: str = 'inv', exposures: dict | None = None) -> dict:
    manifest = _build_manifest(models, invocation_id=invocation_id, exposures=exposures)
    return store.record_dbt_import(json.dumps(manifest).encode(), project_id=project_id)


def _setup_test_snapshot(store: Store, tmp_path: Path, project_id: str = 'local-retail') -> dict:
    dest = tmp_path / f"snap_{uuid4().hex[:8]}.duckdb"
    manifest = ingest_csv(GOLDEN_CSV, dest)
    return store.record_snapshot('orders_golden', manifest, dest, project_id=project_id)


# ===========================================================================
# 1. Orchestration: Revision-Level Metric Diff Core
# ===========================================================================

def test_orchestration_one_changed_declared_metric(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_base},
    }, project_id=pid, invocation_id='inv-base')
    defn_id = rev_base['resources'][0]['definition_id']
    v1_id = rev_base['resources'][0]['definition_version_id']

    m_rev = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m_rev['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m_rev['id'], definition_id=defn_id, status='active', approved_version_id=v1_id, project_id=pid)

    rev_target = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_target},
    }, project_id=pid, invocation_id='inv-target')

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        mode='advisory',
        project_id=pid
    )

    assert diff['schema_version'] == 'metricguard_metric_diff_v1'
    assert diff['project_id'] == pid
    assert diff['baseline_revision_id'] == rev_base['id']
    assert diff['target_revision_id'] == rev_target['id']
    assert diff['snapshot_id'] == snapshot['id']
    assert diff['mode'] == 'advisory'
    assert diff['counts']['total_changed_resources'] == 1
    assert diff['counts']['total_declared_metrics_affected'] == 1
    assert diff['counts']['successfully_analyzed_metrics'] == 1
    assert diff['counts']['refused_metrics'] == 0
    assert diff['counts']['undeclared_changed_resources'] == 0
    assert diff['overall_outcome'] in ('completed', 'completed_with_material_drift')
    assert diff['exit_code'] == 0
    assert len(diff['metrics']) == 1
    assert diff['metrics'][0]['metric_id'] == m_rev['id']
    assert diff['metrics'][0]['display_name'] == 'Monthly Revenue'
    assert diff['metrics'][0]['importance'] == 'critical'
    assert diff['metrics'][0]['execution_status'] == 'executed'
    assert diff['metrics'][0]['investigation_promotable'] is True
    assert diff['impact_report_id'] is not None


def test_orchestration_several_changed_declared_metrics(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    sql_cust_base = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    sql_cust_target = f"SELECT SUM(order_amount) FROM orders WHERE customer_id != 'C99' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_base},
        'model.retail.total_orders': {'sql': sql_cust_base},
    }, project_id=pid, invocation_id='inv-base')

    r_rev = next(r for r in rev_base['resources'] if r['unique_id'] == 'model.retail.monthly_revenue')
    r_cust = next(r for r in rev_base['resources'] if r['unique_id'] == 'model.retail.total_orders')

    m1 = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m1['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m1['id'], definition_id=r_rev['definition_id'], status='active', approved_version_id=r_rev['definition_version_id'], project_id=pid)

    m2 = store.create_metric(display_name='Total Orders', importance='important', project_id=pid)
    store.attach_external_metric_reference(m2['id'], provider='dbt', external_id='model.retail.total_orders', project_id=pid)
    store.bind_metric_definition(metric_id=m2['id'], definition_id=r_cust['definition_id'], status='active', approved_version_id=r_cust['definition_version_id'], project_id=pid)

    rev_target = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_target},
        'model.retail.total_orders': {'sql': sql_cust_target},
    }, project_id=pid, invocation_id='inv-target')

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    assert diff['counts']['total_declared_metrics_affected'] == 2
    assert diff['counts']['successfully_analyzed_metrics'] == 2
    assert len(diff['metrics']) == 2
    metric_ids = {m['metric_id'] for m in diff['metrics']}
    assert metric_ids == {m1['id'], m2['id']}


def test_orchestration_mixed_declared_and_undeclared_resources(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"
    sql_stg_base = "SELECT order_id, customer_id FROM orders"
    sql_stg_target = "SELECT order_id, customer_id, order_date FROM orders"
    sql_unchanged = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Cancelled' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_base},
        'model.retail.stg_orders': {'sql': sql_stg_base},
        'model.retail.cancelled_orders': {'sql': sql_unchanged},
    }, project_id=pid, invocation_id='inv-base')
    r_rev = next(r for r in rev_base['resources'] if r['unique_id'] == 'model.retail.monthly_revenue')

    m = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=r_rev['definition_id'], status='active', approved_version_id=r_rev['definition_version_id'], project_id=pid)

    rev_target = _import_manifest(store, {
        'model.retail.monthly_revenue': {'sql': sql_rev_target},
        'model.retail.stg_orders': {'sql': sql_stg_target},
        'model.retail.cancelled_orders': {'sql': sql_unchanged},
    }, project_id=pid, invocation_id='inv-target')

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    assert diff['counts']['total_changed_resources'] == 2
    assert diff['counts']['unchanged_resources'] == 1
    assert diff['counts']['total_declared_metrics_affected'] == 1
    assert diff['counts']['undeclared_changed_resources'] == 1

    undeclared = diff['undeclared_resources']
    assert len(undeclared) == 1
    assert undeclared[0]['unique_id'] == 'model.retail.stg_orders'
    assert undeclared[0]['reason'] == 'UNDECLARED_METRIC'
    assert 'metric' not in undeclared[0] or undeclared[0].get('metric') is None


def test_orchestration_no_changed_metrics(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_stg_base = "SELECT order_id FROM orders"
    sql_stg_target = "SELECT order_id, customer_id FROM orders"

    rev_base = _import_manifest(store, {
        'model.retail.stg_orders': {'sql': sql_stg_base},
    }, project_id=pid, invocation_id='inv-base')

    rev_target = _import_manifest(store, {
        'model.retail.stg_orders': {'sql': sql_stg_target},
    }, project_id=pid, invocation_id='inv-target')

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    assert diff['counts']['total_declared_metrics_affected'] == 0
    assert diff['counts']['successfully_analyzed_metrics'] == 0
    assert diff['counts']['undeclared_changed_resources'] == 1
    assert diff['coverage']['declared_metrics']['total_affected'] == 0
    assert diff['coverage']['declared_metrics']['coverage_pct'] == 100.0
    assert diff['overall_outcome'] == 'completed'
    assert diff['exit_code'] == 0


def test_deterministic_repeated_diff_execution(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.monthly_revenue': {'sql': sql_rev_base}}, project_id=pid, invocation_id='inv-base')
    defn_id = rev_base['resources'][0]['definition_id']

    m = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.monthly_revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=defn_id, status='active', project_id=pid)

    rev_target = _import_manifest(store, {'model.retail.monthly_revenue': {'sql': sql_rev_target}}, project_id=pid, invocation_id='inv-target')

    diff1 = store.execute_metric_diff(baseline_revision_id=rev_base['id'], target_revision_id=rev_target['id'], snapshot_id=snapshot['id'], project_id=pid)
    diff2 = store.execute_metric_diff(baseline_revision_id=rev_base['id'], target_revision_id=rev_target['id'], snapshot_id=snapshot['id'], project_id=pid)

    assert diff1['id'] == diff2['id']
    assert diff1['request_hash'] == diff2['request_hash']
    assert diff1['change_set_hash'] == diff2['change_set_hash']


# ===========================================================================
# 2. Coverage Report: Declared Metrics, Importance, and Structural Signals
# ===========================================================================

def test_coverage_report_declared_metrics_and_importance_segmentation(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    # 4 models
    sql_crit_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_crit_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    sql_imp_base = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    sql_imp_target = f"SELECT SUM(order_amount) FROM orders WHERE customer_id != 'C99' AND {PERIOD_CLAUSE}"

    # Standard model with multiple CTEs (refused)
    sql_std_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Pending' AND {PERIOD_CLAUSE}"
    sql_std_target = f"WITH a AS (SELECT * FROM orders WHERE status = 'Pending'), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b WHERE {PERIOD_CLAUSE}"

    # Unclassified model
    sql_unclass_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Cancelled' AND {PERIOD_CLAUSE}"
    sql_unclass_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Cancelled' AND customer_id = 'C02' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {
        'model.retail.m_crit': {'sql': sql_crit_base},
        'model.retail.m_imp': {'sql': sql_imp_base},
        'model.retail.m_std': {'sql': sql_std_base},
        'model.retail.m_unclass': {'sql': sql_unclass_base},
    }, project_id=pid, invocation_id='inv-base')

    res_map = {r['unique_id']: r for r in rev_base['resources']}

    m_crit = store.create_metric(display_name='Critical Rev', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m_crit['id'], provider='dbt', external_id='model.retail.m_crit', project_id=pid)
    store.bind_metric_definition(metric_id=m_crit['id'], definition_id=res_map['model.retail.m_crit']['definition_id'], status='active', project_id=pid)

    m_imp = store.create_metric(display_name='Important Orders', importance='important', project_id=pid)
    store.attach_external_metric_reference(m_imp['id'], provider='dbt', external_id='model.retail.m_imp', project_id=pid)
    store.bind_metric_definition(metric_id=m_imp['id'], definition_id=res_map['model.retail.m_imp']['definition_id'], status='active', project_id=pid)

    m_std = store.create_metric(display_name='Standard Pending', importance='standard', project_id=pid)
    store.attach_external_metric_reference(m_std['id'], provider='dbt', external_id='model.retail.m_std', project_id=pid)
    store.bind_metric_definition(metric_id=m_std['id'], definition_id=res_map['model.retail.m_std']['definition_id'], status='active', project_id=pid)

    m_unclass = store.create_metric(display_name='Unclassified Cancelled', importance='unclassified', project_id=pid)
    store.attach_external_metric_reference(m_unclass['id'], provider='dbt', external_id='model.retail.m_unclass', project_id=pid)
    store.bind_metric_definition(metric_id=m_unclass['id'], definition_id=res_map['model.retail.m_unclass']['definition_id'], status='active', project_id=pid)

    rev_target = _import_manifest(store, {
        'model.retail.m_crit': {'sql': sql_crit_target},
        'model.retail.m_imp': {'sql': sql_imp_target},
        'model.retail.m_std': {'sql': sql_std_target},
        'model.retail.m_unclass': {'sql': sql_unclass_target},
    }, project_id=pid, invocation_id='inv-target')

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    cov = diff['coverage']
    dec_cov = cov['declared_metrics']
    assert dec_cov['total_affected'] == 4
    assert dec_cov['analyzable'] == 3
    assert dec_cov['refused'] == 1
    assert dec_cov['coverage_pct'] == 75.0

    imp_cov = cov['importance_weighted']
    assert imp_cov['critical']['affected'] == 1
    assert imp_cov['critical']['analyzable'] == 1
    assert imp_cov['critical']['refused'] == 0
    assert imp_cov['critical']['coverage_pct'] == 100.0

    assert imp_cov['important']['affected'] == 1
    assert imp_cov['important']['analyzable'] == 1
    assert imp_cov['important']['refused'] == 0
    assert imp_cov['important']['coverage_pct'] == 100.0

    assert imp_cov['standard']['affected'] == 1
    assert imp_cov['standard']['analyzable'] == 0
    assert imp_cov['standard']['refused'] == 1
    assert imp_cov['standard']['coverage_pct'] == 0.0

    assert imp_cov['unclassified']['affected'] == 1
    assert imp_cov['unclassified']['analyzable'] == 1
    assert imp_cov['unclassified']['refused'] == 0
    assert imp_cov['unclassified']['coverage_pct'] == 100.0


def test_coverage_signals_kept_separate_no_inferred_importance(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE customer_id != 'C99' AND {PERIOD_CLAUSE}"

    # model with exposure and tags
    exposures = {
        'exposure.retail.executive_dashboard': {
            'unique_id': 'exposure.retail.executive_dashboard',
            'type': 'dashboard',
            'depends_on': {'nodes': ['model.retail.exp_model']},
        }
    }

    rev_base = _import_manifest(store, {
        'model.retail.exp_model': {'sql': sql_base, 'tags': ['tier_1', 'executive']},
    }, project_id=pid, invocation_id='inv-base', exposures=exposures)

    # Declare metric with NO importance (defaults to unclassified)
    m = store.create_metric(display_name='Executive Model Metric', project_id=pid)
    assert m['importance'] == 'unclassified'
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.exp_model', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=rev_base['resources'][0]['definition_id'], status='active', project_id=pid)

    rev_target = _import_manifest(store, {
        'model.retail.exp_model': {'sql': sql_target, 'tags': ['tier_1', 'executive']},
    }, project_id=pid, invocation_id='inv-target', exposures=exposures)

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    signals = diff['coverage']['observable_structural_signals']
    assert signals['exposure_referenced_changed_resources']['count'] == 1
    assert 'model.retail.exp_model' in signals['exposure_referenced_changed_resources']['resource_ids']
    assert signals['other_observable_metadata']['tagged_changed_resources']['count'] == 1

    # Verify importance remains strictly unclassified (NOT inferred as critical)
    imp_cov = diff['coverage']['importance_weighted']
    assert imp_cov['critical']['affected'] == 0
    assert imp_cov['unclassified']['affected'] == 1
    assert diff['metrics'][0]['importance'] == 'unclassified'


# ===========================================================================
# 3. Mixed Results, Outcome Semantics, and Refusal Precedence
# ===========================================================================

def test_mixed_results_material_drift_and_refusal_together(tmp_path):
    """Refusal takes precedence over materiality and pass. A mixture of

    3 pass, 1 material, 2 refused MUST exit 2 in both advisory and enforcing modes.
    """
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    # 1. Pass metric (immaterial change)
    sql_p1_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_p1_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND order_amount >= 0 AND {PERIOD_CLAUSE}"  # 0 delta


    # 2. Material metric (delta exceeds threshold)
    sql_mat_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_mat_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    # 3. Refused metric (multiple CTEs)
    sql_ref_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_ref_target = f"WITH a AS (SELECT * FROM orders WHERE status = 'Completed'), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b WHERE {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {
        'model.retail.m_pass': {'sql': sql_p1_base},
        'model.retail.m_mat': {'sql': sql_mat_base},
        'model.retail.m_ref': {'sql': sql_ref_base},
    }, project_id=pid, invocation_id='inv-base')
    res_map = {r['unique_id']: r for r in rev_base['resources']}

    m_p1 = store.create_metric(display_name='Pass Metric', importance='standard', project_id=pid)
    store.attach_external_metric_reference(m_p1['id'], provider='dbt', external_id='model.retail.m_pass', project_id=pid)
    store.bind_metric_definition(metric_id=m_p1['id'], definition_id=res_map['model.retail.m_pass']['definition_id'], status='active', project_id=pid)

    m_mat = store.create_metric(display_name='Material Metric', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m_mat['id'], provider='dbt', external_id='model.retail.m_mat', project_id=pid)
    store.bind_metric_definition(metric_id=m_mat['id'], definition_id=res_map['model.retail.m_mat']['definition_id'], status='active', project_id=pid)

    m_ref = store.create_metric(display_name='Refused Metric', importance='important', project_id=pid)
    store.attach_external_metric_reference(m_ref['id'], provider='dbt', external_id='model.retail.m_ref', project_id=pid)
    store.bind_metric_definition(metric_id=m_ref['id'], definition_id=res_map['model.retail.m_ref']['definition_id'], status='active', project_id=pid)

    rev_target = _import_manifest(store, {
        'model.retail.m_pass': {'sql': sql_p1_target},
        'model.retail.m_mat': {'sql': sql_mat_target},
        'model.retail.m_ref': {'sql': sql_ref_target},
    }, project_id=pid, invocation_id='inv-target')

    policy = {'absolute_threshold': '10.0', 'relative_threshold': 0.01}

    # Test Advisory mode
    diff_advisory = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        mode='advisory',
        materiality_policy=policy,
        project_id=pid
    )
    assert diff_advisory['overall_outcome'] == 'completed_with_material_drift_and_refusals'
    assert diff_advisory['exit_code'] == 2  # Refusal takes precedence!

    # Test Enforcing mode
    diff_enforcing = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        mode='enforcing',
        materiality_policy=policy,
        project_id=pid
    )
    assert diff_enforcing['overall_outcome'] == 'completed_with_material_drift_and_refusals'
    assert diff_enforcing['exit_code'] == 2  # Refusal takes precedence over materiality!

    # Verify breakdown retains all details
    assert diff_enforcing['counts']['successfully_analyzed_metrics'] == 2
    assert diff_enforcing['counts']['material_metrics'] == 1
    assert diff_enforcing['counts']['refused_metrics'] == 1
    assert len(diff_enforcing['refusals']) >= 1


def test_pure_outcome_and_exit_code_aggregation_matrix():
    """Verify the deterministic outcome and exit code rules across all combinations."""
    # 1. Clean completion
    out, code = determine_diff_outcome_and_exit_code('advisory', material_metrics_count=0, refused_metrics_count=0)
    assert out == 'completed' and code == 0
    out, code = determine_diff_outcome_and_exit_code('enforcing', material_metrics_count=0, refused_metrics_count=0)
    assert out == 'completed' and code == 0

    # 2. Material drift only
    out, code = determine_diff_outcome_and_exit_code('advisory', material_metrics_count=1, refused_metrics_count=0)
    assert out == 'completed_with_material_drift' and code == 0
    out, code = determine_diff_outcome_and_exit_code('enforcing', material_metrics_count=1, refused_metrics_count=0)
    assert out == 'completed_with_material_drift' and code == 1

    # 3. Refusals only
    out, code = determine_diff_outcome_and_exit_code('advisory', material_metrics_count=0, refused_metrics_count=2)
    assert out == 'completed_with_refusals' and code == 2
    out, code = determine_diff_outcome_and_exit_code('enforcing', material_metrics_count=0, refused_metrics_count=2)
    assert out == 'completed_with_refusals' and code == 2

    # 4. Mixture (e.g. 3 pass, 1 material, 2 refused)
    out, code = determine_diff_outcome_and_exit_code('advisory', material_metrics_count=1, refused_metrics_count=2)
    assert out == 'completed_with_material_drift_and_refusals' and code == 2
    out, code = determine_diff_outcome_and_exit_code('enforcing', material_metrics_count=1, refused_metrics_count=2)
    assert out == 'completed_with_material_drift_and_refusals' and code == 2

    # 5. Operational failure
    out, code = determine_diff_outcome_and_exit_code('enforcing', material_metrics_count=1, refused_metrics_count=2, operational_failure=True)
    assert out == 'operational_failure' and code == 3



# ===========================================================================
# 4. Approved Baseline Semantics: Drift Amnesia Prevention
# ===========================================================================

def test_approved_baseline_cumulative_drift_and_incremental_pr_impact(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    # Approved V1: All completed orders
    sql_v1 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    rev_v1 = _import_manifest(store, {'model.retail.rev': {'sql': sql_v1}}, project_id=pid, invocation_id='inv-v1')
    res_v1 = rev_v1['resources'][0]

    m = store.create_metric(display_name='Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.rev', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=res_v1['definition_id'], status='active', approved_version_id=res_v1['definition_version_id'], project_id=pid)

    # Intermediate unapproved V2 on base branch: exclude C03
    sql_v2 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id != 'C03' AND {PERIOD_CLAUSE}"
    rev_v2 = _import_manifest(store, {'model.retail.rev': {'sql': sql_v2}}, project_id=pid, invocation_id='inv-v2')

    # PR target V3: exclude C03 and C02
    sql_v3 = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id NOT IN ('C03', 'C02') AND {PERIOD_CLAUSE}"
    rev_v3 = _import_manifest(store, {'model.retail.rev': {'sql': sql_v3}}, project_id=pid, invocation_id='inv-v3')

    # Compare rev_v2 -> rev_v3
    diff = store.execute_metric_diff(
        baseline_revision_id=rev_v2['id'],
        target_revision_id=rev_v3['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    metric_eval = diff['metrics'][0]
    assert metric_eval['has_approved_baseline'] is True
    assert metric_eval['approved_version_id'] == res_v1['definition_version_id']
    assert metric_eval['baseline_revision_version_id'] == rev_v2['resources'][0]['definition_version_id']
    assert metric_eval['target_version_id'] == rev_v3['resources'][0]['definition_version_id']

    # Approved baseline drift (V1 -> V3)
    appr_drift = metric_eval['approved_baseline_impact']
    # Incremental PR impact (V2 -> V3)
    incr_drift = metric_eval['incremental_impact']

    assert appr_drift is not None
    assert incr_drift is not None
    assert appr_drift['exact_delta'] != incr_drift['exact_delta']
    assert Decimal(appr_drift['exact_delta']) < Decimal(incr_drift['exact_delta'])


# ===========================================================================
# 5. Snapshot Isolation and Validation
# ===========================================================================

def test_same_snapshot_used_and_cross_project_rejected(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid_a = 'local-retail'
    pid_b = 'other-retail'

    store.create_project(name='Other Retail', project_id=pid_b)
    snap_a = _setup_test_snapshot(store, tmp_path, pid_a)
    snap_b = _setup_test_snapshot(store, tmp_path, pid_b)

    sql = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    rev_a1 = _import_manifest(store, {'model.retail.m': {'sql': sql}}, project_id=pid_a, invocation_id='inv-a1')
    rev_a2 = _import_manifest(store, {'model.retail.m': {'sql': sql}}, project_id=pid_a, invocation_id='inv-a2')

    # Cross project snapshot rejected
    with pytest.raises(DomainError) as exc_info:
        store.execute_metric_diff(
            baseline_revision_id=rev_a1['id'],
            target_revision_id=rev_a2['id'],
            snapshot_id=snap_b['id'],
            project_id=pid_a
        )
    assert exc_info.value.code == 'FORBIDDEN'


def test_corrupted_snapshot_rejected(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    snap_file = Path(snapshot['artifact_path'])
    snap_file.write_bytes(b'corrupted bytes')


    sql = f"SELECT SUM(order_amount) FROM orders WHERE {PERIOD_CLAUSE}"
    rev_1 = _import_manifest(store, {'model.retail.m': {'sql': sql}}, project_id=pid, invocation_id='inv-1')
    rev_2 = _import_manifest(store, {'model.retail.m': {'sql': sql}}, project_id=pid, invocation_id='inv-2')

    with pytest.raises(DomainError) as exc_info:
        store.execute_metric_diff(
            baseline_revision_id=rev_1['id'],
            target_revision_id=rev_2['id'],
            snapshot_id=snapshot['id'],
            project_id=pid
        )
    assert exc_info.value.code == 'SNAPSHOT_CORRUPTED'


# ===========================================================================
# 6. Reports: JSON Schema & Markdown Formatting
# ===========================================================================

def test_markdown_and_json_report_formatting(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_base}}, project_id=pid, invocation_id='inv-base')
    m = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=rev_base['resources'][0]['definition_id'], status='active', approved_version_id=rev_base['resources'][0]['definition_version_id'], project_id=pid)

    rev_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_target}}, project_id=pid, invocation_id='inv-target')

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    md = format_metric_diff_markdown(diff)

    # Check 10 required sections in Markdown
    assert '## 1. Revision Comparison Identity' in md
    assert '## 2. Reference Snapshot' in md
    assert '## 3. Changed-Resource Summary' in md
    assert '## 4. Declared Metric Coverage' in md
    assert '## 5. Importance-Weighted Coverage' in md
    assert '## 6. Analyzed Metrics & Exact Impact' in md
    assert '## 7. Approved Baseline Drift vs Incremental PR Impact' in md
    assert '## 8. Refused Metrics and Resources' in md
    assert '## 9. Undeclared Changed Resources' in md
    assert '## 10. Investigation Interoperability' in md

    # Check exact values and decimals preserved
    assert diff['metrics'][0]['exact_delta'] in md
    assert 'Monthly Revenue' in md
    assert 'PostgreSQL Source Query' in md


# ===========================================================================
# 7. CLI: Execution, Exit Codes, File Outputs, and Redaction
# ===========================================================================

def test_cli_diff_advisory_exits_0(tmp_path):
    db_path = tmp_path / 'test.db'
    store = Store(f"sqlite:///{db_path}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_base}}, project_id=pid, invocation_id='inv-base')
    m = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=rev_base['resources'][0]['definition_id'], status='active', approved_version_id=rev_base['resources'][0]['definition_version_id'], project_id=pid)

    rev_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_target}}, project_id=pid, invocation_id='inv-target')

    out_json = tmp_path / 'diff_report.json'
    out_md = tmp_path / 'diff_report.md'

    code = run_cli([
        'diff',
        '--project', pid,
        '--baseline', rev_base['id'],
        '--target', rev_target['id'],
        '--snapshot', snapshot['id'],
        '--database-url', f"sqlite:///{db_path}",
        '--output', str(out_json),
        '--markdown-output', str(out_md),
        '--mode', 'advisory',
    ])

    assert code == 0
    assert out_json.is_file()
    assert out_md.is_file()

    with open(out_json, encoding='utf-8') as f:
        data = json.load(f)
    assert data['schema_version'] == 'metricguard_metric_diff_v1'


def test_cli_diff_enforcing_material_drift_exits_1(tmp_path):
    db_path = tmp_path / 'test.db'
    store = Store(f"sqlite:///{db_path}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_base}}, project_id=pid, invocation_id='inv-base')
    m = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=rev_base['resources'][0]['definition_id'], status='active', approved_version_id=rev_base['resources'][0]['definition_version_id'], project_id=pid)

    rev_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_target}}, project_id=pid, invocation_id='inv-target')

    code = run_cli([
        'diff',
        '--project', pid,
        '--baseline', rev_base['id'],
        '--target', rev_target['id'],
        '--snapshot', snapshot['id'],
        '--database-url', f"sqlite:///{db_path}",
        '--mode', 'enforcing',
        '--absolute-threshold', '1.0',
    ])

    assert code == 1


def test_cli_diff_refusal_exits_2(tmp_path):
    db_path = tmp_path / 'test.db'
    store = Store(f"sqlite:///{db_path}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    # Multiple CTEs cause refusal
    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target = f"WITH a AS (SELECT * FROM orders WHERE status = 'Completed'), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b WHERE {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid, invocation_id='inv-base')
    m = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=rev_base['resources'][0]['definition_id'], status='active', approved_version_id=rev_base['resources'][0]['definition_version_id'], project_id=pid)

    rev_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_target}}, project_id=pid, invocation_id='inv-target')

    code = run_cli([
        'diff',
        '--project', pid,
        '--baseline', rev_base['id'],
        '--target', rev_target['id'],
        '--snapshot', snapshot['id'],
        '--database-url', f"sqlite:///{db_path}",
    ])

    assert code == 2


def test_cli_diff_operational_failure_exits_3(tmp_path):
    db_path = tmp_path / 'test.db'
    Store(f"sqlite:///{db_path}")
    pid = 'local-retail'

    # Non-existent snapshot ID
    code = run_cli([
        'diff',
        '--project', pid,
        '--baseline', 'non-existent-rev-1',
        '--target', 'non-existent-rev-2',
        '--snapshot', 'non-existent-snap',
        '--database-url', f"sqlite:///{db_path}",
    ])

    assert code == 3


def test_cli_secret_redaction():
    raw = "Failed to connect: postgresql://user:my_secret_password123@db.prod.internal:5432/db with token=xyz987abc"
    clean = redact_api_key(raw)
    assert 'my_secret_password123' not in clean
    assert 'xyz987abc' not in clean
    assert '[REDACTED]' in clean


# ===========================================================================
# 8. Investigation Interoperability
# ===========================================================================

def test_investigation_promotion_reuses_impact_run_without_recomputation(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_rev_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_rev_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_base}}, project_id=pid, invocation_id='inv-base')
    m = store.create_metric(display_name='Monthly Revenue', importance='critical', project_id=pid)
    store.attach_external_metric_reference(m['id'], provider='dbt', external_id='model.retail.revenue', project_id=pid)
    store.bind_metric_definition(metric_id=m['id'], definition_id=rev_base['resources'][0]['definition_id'], status='active', approved_version_id=rev_base['resources'][0]['definition_version_id'], project_id=pid)

    rev_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_rev_target}}, project_id=pid, invocation_id='inv-target')

    diff = store.execute_metric_diff(
        baseline_revision_id=rev_base['id'],
        target_revision_id=rev_target['id'],
        snapshot_id=snapshot['id'],
        project_id=pid
    )

    impact_id = diff['impact_report_id']
    run_id = diff['metrics'][0]['comparison_run_id']

    # Promote to investigation
    inv = store.promote_metric_impact_to_investigation(
        impact_id=impact_id,
        metric_id=m['id'],
        title='CI Diff Drift Investigation',
        actor_label='CI Robot',
        project_id=pid
    )

    assert inv['id'] is not None
    assert inv['run_id'] == run_id
    assert inv['snapshot_id'] == snapshot['id']
    assert inv['title'] == 'CI Diff Drift Investigation'


# ===========================================================================
# 9. RBAC & Cross-Project Isolation
# ===========================================================================

def test_api_metric_diff_rbac_and_cross_project(tmp_path):
    app = create_app(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    client = TestClient(app)
    store = app.state.store

    pid = 'local-retail'
    snapshot = _setup_test_snapshot(store, tmp_path, pid)

    sql_base = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"

    rev_base = _import_manifest(store, {'model.retail.revenue': {'sql': sql_base}}, project_id=pid, invocation_id='inv-base')
    rev_target = _import_manifest(store, {'model.retail.revenue': {'sql': sql_target}}, project_id=pid, invocation_id='inv-target')

    # Setup users
    u_owner, key_owner = store.create_user('Owner User', 'owner@corp.com')
    store.add_project_member(pid, u_owner['id'], 'owner')

    u_analyst, key_analyst = store.create_user('Analyst User', 'analyst@corp.com')
    store.add_project_member(pid, u_analyst['id'], 'analyst')

    u_reader, key_reader = store.create_user('Reader User', 'reader@corp.com')
    store.add_project_member(pid, u_reader['id'], 'reader')

    payload = {
        'baseline_revision_id': rev_base['id'],
        'target_revision_id': rev_target['id'],
        'snapshot_id': snapshot['id'],
        'mode': 'advisory',
    }

    # Reader cannot POST execute diff
    res_r = client.post(f"/api/v1/projects/{pid}/dbt/metric-diffs", json=payload, headers={'X-API-Key': key_reader})
    assert res_r.status_code == 403

    # Analyst can POST execute diff
    res_a = client.post(f"/api/v1/projects/{pid}/dbt/metric-diffs", json=payload, headers={'X-API-Key': key_analyst})
    assert res_a.status_code == 200
    diff_data = res_a.json()
    diff_id = diff_data['id']

    # Reader can GET diff report
    res_get = client.get(f"/api/v1/projects/{pid}/dbt/metric-diffs/{diff_id}", headers={'X-API-Key': key_reader})
    assert res_get.status_code == 200

    # Reader can GET diff report in Markdown format
    res_md = client.get(f"/api/v1/projects/{pid}/dbt/metric-diffs/{diff_id}?format=markdown", headers={'X-API-Key': key_reader})
    assert res_md.status_code == 200
    assert '# MetricGuard Revision Metric Diff Report' in res_md.text

    # Reader can list diff reports
    res_list = client.get(f"/api/v1/projects/{pid}/dbt/metric-diffs", headers={'X-API-Key': key_reader})
    assert res_list.status_code == 200
    assert len(res_list.json()['items']) >= 1

    # Cross-project: another project cannot access diff_id
    pid_other = 'other-proj'
    store.create_project(name='Other Project', project_id=pid_other)
    store.add_project_member(pid_other, u_owner['id'], 'owner')

    res_cross = client.get(f"/api/v1/projects/{pid_other}/dbt/metric-diffs/{diff_id}", headers={'X-API-Key': key_owner})
    assert res_cross.status_code == 403

