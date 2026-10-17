"""Focused verification suite for the Pre-V5 Analytical Coverage Study Evaluator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_coverage import (
    ResourceEvaluation,
    _classify_refusal_reasons,
    _detect_level1_normalization_candidate,
    _inspect_sql_ast,
    aggregate_coverage_results,
    evaluate_manifest_content,
)
from services.api.metadata_adapters import parse_dbt_project_artifacts


def _make_sample_manifest(
    models: dict | None = None,
    metrics: dict | None = None,
    exposures: dict | None = None,
    child_map: dict | None = None,
) -> bytes:
    data = {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11.json",
            "dbt_version": "1.7.0",
            "project_name": "coverage_test_project",
            "generated_at": "2026-09-25T12:00:00Z",
            "invocation_id": "inv-cov-001",
        },
        "nodes": models or {},
        "metrics": metrics or {},
        "exposures": exposures or {},
        "child_map": child_map or {},
    }
    return json.dumps(data).encode("utf-8")


# 1. Verify production classifier is reused
def test_production_classifier_is_reused():
    manifest_bytes = _make_sample_manifest(
        models={
            "model.test.exec": {
                "unique_id": "model.test.exec",
                "resource_type": "model",
                "name": "exec",
                "original_file_path": "models/exec.sql",
                "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                "depends_on": {"nodes": []},
            },
            "model.test.cte": {
                "unique_id": "model.test.cte",
                "resource_type": "model",
                "name": "cte",
                "original_file_path": "models/cte.sql",
                "compiled_code": "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b",
                "depends_on": {"nodes": []},
            },
        }
    )
    # Production adapter result
    prod_parsed = parse_dbt_project_artifacts(manifest_bytes)
    prod_states = {r["unique_id"]: r["support_state"] for r in prod_parsed["resources"]}

    # Coverage evaluator result
    cov_result = evaluate_manifest_content(manifest_bytes)
    cov_states = {r.unique_id: r.support_state for r in cov_result.resources}

    assert cov_states == prod_states
    assert cov_states["model.test.exec"] == "executable"
    assert cov_states["model.test.cte"] == "metadata_only"


# 2. Verify deterministic report output
def test_deterministic_report_output():
    manifest_bytes = _make_sample_manifest(
        models={
            "model.test.m1": {
                "unique_id": "model.test.m1",
                "resource_type": "model",
                "name": "m1",
                "original_file_path": "models/m1.sql",
                "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                "depends_on": {"nodes": []},
            }
        }
    )
    res1 = evaluate_manifest_content(manifest_bytes)
    res2 = evaluate_manifest_content(manifest_bytes)

    agg1 = aggregate_coverage_results([res1])
    agg2 = aggregate_coverage_results([res2])

    assert json.dumps(agg1, sort_keys=True) == json.dumps(agg2, sort_keys=True)


# 3. Verify multiple refusal reasons handled correctly and primary precedence holds
def test_multiple_refusal_reasons_and_precedence():
    # Query with both CTE and JOIN
    sql_cte_join = "WITH c AS (SELECT * FROM orders) SELECT SUM(c.order_amount) FROM c JOIN customers cust ON c.customer_id = cust.id"
    ast_info = _inspect_sql_ast(sql_cte_join)
    assert ast_info["has_cte"] is True
    assert ast_info["has_join"] is True

    diags = [
        "CTE_DETECTED: Common Table Expressions present",
        "JOIN_DETECTED: Multi-table JOINs detected",
    ]
    all_reasons, primary = _classify_refusal_reasons("metadata_only", diags, ast_info, "model")

    assert "cte" in all_reasons
    assert "join" in all_reasons
    # Under precedence order (cte before join), cte is primary
    assert primary == "cte"


# 4. Verify explicit metric and exposure detection
def test_explicit_metric_and_exposure_detection():
    manifest_bytes = _make_sample_manifest(
        models={
            "model.test.exposed_model": {
                "unique_id": "model.test.exposed_model",
                "resource_type": "model",
                "name": "exposed_model",
                "original_file_path": "models/marts/exposed.sql",
                "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                "depends_on": {"nodes": []},
            },
            "model.test.unexposed_model": {
                "unique_id": "model.test.unexposed_model",
                "resource_type": "model",
                "name": "unexposed_model",
                "original_file_path": "models/staging/unexposed.sql",
                "compiled_code": "SELECT id FROM orders",
                "depends_on": {"nodes": []},
            },
        },
        metrics={
            "metric.test.revenue": {
                "unique_id": "metric.test.revenue",
                "resource_type": "metric",
                "name": "revenue",
                "label": "Revenue",
                "calculation_method": "sum",
                "expression": "order_amount",
                "timestamp": "order_date",
                "depends_on": {"nodes": ["model.test.exposed_model"]},
            }
        },
        exposures={
            "exposure.test.dash": {
                "unique_id": "exposure.test.dash",
                "name": "dash",
                "type": "dashboard",
                "depends_on": {"nodes": ["model.test.exposed_model"]},
            }
        },
    )
    cov = evaluate_manifest_content(manifest_bytes)
    res_map = {r.unique_id: r for r in cov.resources}

    # Exposure check
    assert res_map["model.test.exposed_model"].referenced_by_exposure is True
    assert res_map["model.test.unexposed_model"].referenced_by_exposure is False

    # Explicit metric check
    assert res_map["metric.test.revenue"].is_explicit_metric is True
    assert res_map["model.test.exposed_model"].is_explicit_metric is False

    # Summary views
    assert cov.view_c_explicit_metrics.total == 1
    assert cov.view_d_exposure_referenced.total == 1


# 5. Verify Level-1 candidate detection is conservative and never alters executable state
def test_level1_candidate_detection_is_conservative():
    # Valid conservative candidate: single-use filter CTE over single table with outer SUM
    cand_sql = "WITH qualified AS (SELECT * FROM orders WHERE status = 'delivered') SELECT SUM(order_amount) FROM qualified"
    is_cand, notes = _detect_level1_normalization_candidate(cand_sql, ["cte"])
    assert is_cand is True
    assert "Candidate" in str(notes)

    # REJECTED: Multiple CTEs
    multi_cte_sql = "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) SELECT SUM(order_amount) FROM b"
    is_cand_multi, notes_multi = _detect_level1_normalization_candidate(multi_cte_sql, ["cte"])
    assert is_cand_multi is False
    assert "not exactly one CTE" in str(notes_multi)

    # REJECTED: JOIN inside CTE
    join_cte_sql = "WITH joined AS (SELECT o.order_amount FROM orders o JOIN cust c ON o.customer_id = c.id) SELECT SUM(order_amount) FROM joined"
    is_cand_join, notes_join = _detect_level1_normalization_candidate(join_cte_sql, ["cte"])
    assert is_cand_join is False
    assert "JOIN inside CTE" in str(notes_join)

    # REJECTED: Aggregation inside CTE (changes grain)
    agg_cte_sql = "WITH agg AS (SELECT customer_id, SUM(order_amount) as total FROM orders GROUP BY 1) SELECT SUM(total) FROM agg"
    is_cand_agg, notes_agg = _detect_level1_normalization_candidate(agg_cte_sql, ["cte"])
    assert is_cand_agg is False
    assert "aggregation inside CTE" in str(notes_agg)

    # REJECTED: Outer query is AVG, not SUM/COUNT
    avg_cte_sql = "WITH qualified AS (SELECT * FROM orders) SELECT AVG(order_amount) FROM qualified"
    is_cand_avg, notes_avg = _detect_level1_normalization_candidate(avg_cte_sql, ["cte"])
    assert is_cand_avg is False
    assert "not SUM or COUNT" in str(notes_avg)


# 6. Verify NO Level-1 candidate is accidentally marked executable
def test_no_candidate_accidentally_marked_executable():
    manifest_bytes = _make_sample_manifest(
        models={
            "model.test.cand": {
                "unique_id": "model.test.cand",
                "resource_type": "model",
                "name": "cand",
                "original_file_path": "models/cand.sql",
                "compiled_code": "WITH qualified AS (SELECT * FROM subscription_periods WHERE status = 'delivered') SELECT SUM(order_amount) FROM qualified",
                "depends_on": {"nodes": []},
            }
        }
    )
    cov = evaluate_manifest_content(manifest_bytes)
    res = cov.resources[0]

    assert res.is_level1_normalization_candidate is True
    # Invariant: Support state remains metadata_only, is_executable remains False
    assert res.support_state == "metadata_only"
    assert res.is_executable is False
    assert cov.view_a_all_models.executable == 0


# 7. Verify no business importance inference occurs
def test_no_importance_inference_occurs():
    manifest_bytes = _make_sample_manifest(
        models={
            "model.test.leaf": {
                "unique_id": "model.test.leaf",
                "resource_type": "model",
                "name": "cfo_critical_revenue",
                "original_file_path": "models/marts/cfo_critical_revenue.sql",
                "compiled_code": "SELECT SUM(order_amount) FROM orders WHERE order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'",
                "depends_on": {"nodes": []},
            }
        },
        exposures={
            "exposure.test.board": {
                "unique_id": "exposure.test.board",
                "name": "board_deck",
                "type": "dashboard",
                "depends_on": {"nodes": ["model.test.leaf"]},
            }
        },
    )
    cov = evaluate_manifest_content(manifest_bytes)
    res = cov.resources[0]

    # Evaluator records observable structural proxies only
    assert res.referenced_by_exposure is True
    assert res.is_analytical_final_proxy is True

    # No importance fields exist on ResourceEvaluation (no critical/important/CFO classification)
    eval_dict = res.__dict__
    assert "importance" not in eval_dict
    assert "business_critical" not in eval_dict
    assert "cfo_facing" not in eval_dict
