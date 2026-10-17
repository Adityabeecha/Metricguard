"""Comprehensive test suite for MetricGuard V6.3:
Turnkey CI / PR Metric Change Gate.

Covers:
1. Manifest Diff Resolution (base vs target manifest change tracking)
2. Affected Metric Resolution & Mapping to metricguard.yml
3. Trusted Base Policy vs Candidate Non-Authority (Security Critical)
4. Candidate Proposed Configuration Changes Reporting (fail -> ignore, binding addition/removal)
5. Effective Policy Artifact & Immutability
6. Exit Code Semantics & Mixture Precedence:
   - 0: Clean / advisory completion
   - 1: Enforcing material drift
   - 2: Analytical refusal (precedence over materiality)
   - 3: Operational failure
7. Acceptance Cases A through G (Clean, Material, Refusal, Operational, Self-Weakening, App URL Injection, Headless)
8. Deterministic JSON Report Schema (metricguard_ci_report_v1)
9. Zero-Green Markdown Report Generation (🔵, 🔴, 🟠, ⚪)
10. Secret Redaction across all channels
11. Headless Operation & App URL Trust Enforcement
12. GitHub Adapter Idempotent Commenting (<!-- metricguard-ci-report -->) & Outputs
13. Fork PR Security & Graceful Degradation
14. High-resolution Timing Profiling
15. CLI `metricguard diff-ci` invocation via subprocess and direct call
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from packages.metricguard_core import ingest_csv
from services.api.store import DomainError, Store, metrics as metrics_tbl
from services.ci_workflow import (
    CI_REPORT_SCHEMA,
    CI_REPORT_VERSION,
    PR_COMMENT_MARKER,
    diff_repository_configs,
    execute_diff_ci,
    format_ci_markdown_report,
    redact_ci_secrets,
)
from services.cli import run_cli
from services.config import ConfigErrorCode, ConfigProvenance, load_config_from_str
from scripts.github_ci_adapter import find_existing_comment_id, post_or_update_pr_comment, run_adapter

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CSV = ROOT / "MetricGuard_Planning_Pack" / "examples" / "orders_golden.csv"
PERIOD_CLAUSE = "order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def _build_manifest(models: dict, version: int = 12) -> dict:
    """Build a synthetic dbt manifest with specified models."""
    compiled_key = "compiled_code" if version >= 9 else "compiled_sql"
    nodes = {}
    for uid, info in models.items():
        nodes[uid] = {
            "unique_id": uid,
            "resource_type": "model",
            "name": uid.split(".")[-1],
            "original_file_path": f"models/{uid}.sql",
            "raw_code": info.get("raw_code", "SELECT 1"),
            compiled_key: info.get("sql"),
            "depends_on": {"nodes": []},
            "meta": {},
            "tags": [],
        }
    return {
        "metadata": {
            "dbt_schema_version": f"https://schemas.getdbt.com/dbt/manifest/v{version}.json",
            "dbt_version": f"1.{version}.0",
            "project_name": "retail_analytics",
            "generated_at": "2026-09-26T08:00:00Z",
            "invocation_id": f"inv-{uuid4().hex[:8]}",
        },
        "nodes": nodes,
        "metrics": {},
        "child_map": {},
    }


@pytest.fixture
def ci_environment(tmp_path):
    """
    Sets up a complete isolated CI environment:
    - Initialized Store with workspace 'retail'
    - Seeded golden snapshot
    - Governed metrics:
      1. 'metric-rev-1' (Gross Revenue, critical, 5% relative threshold)
      2. 'metric-cnt-2' (Order Count, important, 50 absolute threshold)
    """
    db_path = tmp_path / "store.db"
    db_url = f"sqlite:///{db_path}"
    store = Store(db_url)
    pid = "retail"
    store.create_project(name="Retail Analytics", project_id=pid)

    # Ingest golden snapshot
    snap_dest = tmp_path / "orders_golden.duckdb"
    snap_manifest = ingest_csv(GOLDEN_CSV, snap_dest)
    snapshot = store.record_snapshot("orders_golden", snap_manifest, str(snap_dest), project_id=pid)

    # Create Governed Metrics
    m_rev = store.create_metric(
        display_name="Gross Revenue",
        importance="critical",
        metric_id="metric-rev-1",
        project_id=pid,
    )

    m_cnt = store.create_metric(
        display_name="Order Count",
        importance="important",
        metric_id="metric-cnt-2",
        project_id=pid,
    )

    # SQL definitions
    sql_base_rev = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target_rev_material = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"
    sql_target_rev_clean = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND status != 'Cancelled' AND {PERIOD_CLAUSE}"
    sql_target_rev_unsupported = f"SELECT SUM(order_amount) FROM orders JOIN orders b USING (order_id) WHERE {PERIOD_CLAUSE}"

    sql_base_cnt = f"SELECT COUNT(*) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target_cnt_clean = f"SELECT COUNT(*) FROM orders WHERE status = 'Completed' AND status != 'Cancelled' AND {PERIOD_CLAUSE}"

    return {
        "store": store,
        "db_url": db_url,
        "pid": pid,
        "snapshot_id": snapshot["id"],
        "snapshot_path": snap_dest,
        "sql_base_rev": sql_base_rev,
        "sql_target_rev_material": sql_target_rev_material,
        "sql_target_rev_clean": sql_target_rev_clean,
        "sql_target_rev_unsupported": sql_target_rev_unsupported,
        "sql_base_cnt": sql_base_cnt,
        "sql_target_cnt_clean": sql_target_cnt_clean,
    }


# ---------------------------------------------------------------------------
# 1. Config Diffing & Security Boundaries
# ---------------------------------------------------------------------------

class TestConfigDiffingAndSecurity:
    def test_detect_proposed_ci_action_change(self):
        """Candidate PR proposing ci_action change fail -> ignore is detected."""
        base_yaml = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
"""
        cand_yaml = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "ignore"
"""
        base_cfg, _ = load_config_from_str(base_yaml)
        cand_cfg, _ = load_config_from_str(cand_yaml)

        changes = diff_repository_configs(base_cfg.config, cand_cfg.config)
        assert len(changes) == 1
        assert changes[0].category == "metric_binding_modified"
        assert changes[0].item == "metrics[metric-rev-1].ci_action"
        assert changes[0].base_value == "fail"
        assert changes[0].candidate_value == "ignore"
        assert "fail" in changes[0].description
        assert "ignore" in changes[0].description

    def test_detect_proposed_binding_addition_and_removal(self):
        """Candidate adding and removing bindings is cleanly categorized."""
        base_yaml = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-1"
    dbt_model: "model.retail.m1"
"""
        cand_yaml = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-2"
    dbt_model: "model.retail.m2"
"""
        base_cfg, _ = load_config_from_str(base_yaml)
        cand_cfg, _ = load_config_from_str(cand_yaml)

        changes = diff_repository_configs(base_cfg.config, cand_cfg.config)
        categories = {c.category for c in changes}
        assert "metric_binding_removed" in categories
        assert "metric_binding_added" in categories

    def test_detect_proposed_app_url_and_snapshot_change(self):
        """Candidate modifying snapshot or app URL is detected."""
        base_yaml = """
version: 1
workspace: retail
snapshot:
  snapshot_id: "snap-base"
app:
  base_url: "https://metricguard.corp.internal"
"""
        cand_yaml = """
version: 1
workspace: retail
snapshot:
  snapshot_id: "snap-cand"
app:
  base_url: "https://evil-phishing.internal"
"""
        base_cfg, _ = load_config_from_str(base_yaml)
        cand_cfg, _ = load_config_from_str(cand_yaml)

        changes = diff_repository_configs(base_cfg.config, cand_cfg.config)
        items = {c.item for c in changes}
        assert "snapshot" in items
        assert "app.base_url" in items


# ---------------------------------------------------------------------------
# 2. Acceptance Cases A through G
# ---------------------------------------------------------------------------

class TestAcceptanceCases:
    def test_acceptance_case_a_clean_pass(self, ci_environment, tmp_path):
        """CASE A — CLEAN: All metrics analyzed without material drift -> exit 0."""
        # Setup manifests
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_clean"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 0
        assert res.overall_outcome == "completed"
        assert res.report["counts"]["material_metrics"] == 0
        assert res.report["counts"]["refused_metrics"] == 0
        assert "🔵 **Analyzed — Clean**" in res.markdown
        assert "Gross Revenue" in res.markdown

    def test_acceptance_case_b_material_critical_metric(self, ci_environment, tmp_path):
        """CASE B — MATERIAL CRITICAL METRIC: Material drift on critical metric -> exit 1."""
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        # Materially different target SQL
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_material"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 1
        assert res.overall_outcome == "completed_with_material_drift"
        assert res.report["counts"]["material_metrics"] == 1
        assert "🔴 **Material Drift Exceeded**" in res.markdown
        assert "🔴 Material Changes" in res.markdown

    def test_acceptance_case_c_analytical_refusal(self, ci_environment, tmp_path):
        """CASE C — REFUSAL: Metric uses unsupported SQL (JOIN) -> exit 2."""
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        # Target SQL uses unsupported JOIN
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_unsupported"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 2
        assert res.overall_outcome == "completed_with_refusals"
        assert res.report["counts"]["refused_metrics"] == 1
        assert "🟠 **Analytical Refusal**" in res.markdown
        assert "🟠 Analytical Refusals" in res.markdown

    def test_acceptance_case_d_operational_failure(self, ci_environment, tmp_path):
        """CASE D — OPERATIONAL FAILURE: Missing manifest or store error -> exit 3."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("version: 1\nworkspace: retail\n", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=tmp_path / "nonexistent_base.json",
            target_manifest_path=tmp_path / "nonexistent_target.json",
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 3
        assert res.overall_outcome == "operational_failure"
        assert "❌ **Operational Failure**" in res.markdown
        assert any(i.code == ConfigErrorCode.INVALID_MANIFEST.value for i in res.issues)

    def test_acceptance_case_e_policy_self_weakening_attempt(self, ci_environment, tmp_path):
        """
        CASE E — POLICY SELF-WEAKENING ATTEMPT:
        Base has ci_action: fail. Candidate PR changes it to ci_action: ignore.
        Material drift exists.
        Result: Evaluation is STILL governed by fail -> exits 1!
        Report separately states candidate proposed the policy change.
        """
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_material"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        # Trusted base config specifies ci_action: fail
        base_cfg_file = tmp_path / "base_metricguard.yml"
        base_cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        # Candidate PR attempts to weaken ci_action to ignore
        cand_cfg_file = tmp_path / "candidate_metricguard.yml"
        cand_cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "ignore"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cand_cfg_file,
            base_config_path=base_cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
            provenance=ConfigProvenance.UNTRUSTED_CANDIDATE,
        )

        # Enforced using trusted base policy (fail) -> exits 1
        assert res.exit_code == 1
        assert res.overall_outcome == "completed_with_material_drift"

        # Report records proposed configuration change
        assert len(res.report["proposed_config_changes"]) == 1
        p_chg = res.report["proposed_config_changes"][0]
        assert p_chg["base_value"] == "fail"
        assert p_chg["candidate_value"] == "ignore"
        assert "Proposed Repository Configuration Changes" in res.markdown
        assert "fail" in res.markdown
        assert "ignore" in res.markdown

    def test_acceptance_case_f_app_url_injection(self, ci_environment, tmp_path):
        """
        CASE F — APP URL INJECTION:
        Candidate PR adds untrusted phishing app_url.
        Result: Untrusted URL is NEVER used for Studio links.
        """
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_clean"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cand_cfg_file = tmp_path / "candidate_metricguard.yml"
        cand_cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
app:
  base_url: "https://evil-phishing.internal"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cand_cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
            provenance=ConfigProvenance.UNTRUSTED_CANDIDATE,
        )

        assert res.exit_code == 0
        assert res.report["app_url_trusted"] is False
        assert "https://evil-phishing.internal" not in res.markdown
        assert "View in MetricGuard Studio" not in res.markdown

    def test_acceptance_case_g_headless_mode(self, ci_environment, tmp_path, monkeypatch):
        """
        CASE G — HEADLESS:
        No app URL configured anywhere.
        Result: Analysis succeeds cleanly, exit code is correct, no Studio deep-link CTA.
        """
        monkeypatch.delenv("METRICGUARD_APP_URL", raising=False)
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_clean"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 0
        assert res.report["app_url"] is None
        assert "View in MetricGuard Studio" not in res.markdown
        assert "🔵 **Analyzed — Clean**" in res.markdown

    def test_case_h_candidate_removes_own_binding(self, ci_environment, tmp_path):
        """
        CASE H: Candidate PR deletes its own metric binding.
        Result: Base configuration remains authoritative. The binding is still evaluated.
        Removal is reported under proposed configuration changes.
        """
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_clean"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        base_cfg_file = tmp_path / "base_metricguard.yml"
        base_cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        # Candidate deletes metric-rev-1
        cand_cfg_file = tmp_path / "cand_metricguard.yml"
        cand_cfg_file.write_text(f"""
version: 1
workspace: retail
metrics: []
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cand_cfg_file,
            base_config_path=base_cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 0
        assert len(res.report["proposed_config_changes"]) == 1
        assert res.report["proposed_config_changes"][0]["category"] == "metric_binding_removed"
        assert len(res.report["metrics"]) == 1  # Base metric was still evaluated


# ---------------------------------------------------------------------------
# 3. Multiple Metrics & Mixture Precedence
# ---------------------------------------------------------------------------

class TestMultipleMetricsAndMixtures:
    def test_mixture_material_drift_and_refusal_exits_2(self, ci_environment, tmp_path):
        """
        When one metric has material drift and another metric has an analytical refusal:
        Refusal takes precedence -> exits with code 2.
        """
        # Base manifest has 2 models
        base_m = _build_manifest({
            "model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]},
            "model.retail.order_count": {"sql": ci_environment["sql_base_cnt"]},
        })
        # Target manifest has material drift on fct_orders and unsupported JOIN on order_count
        target_m = _build_manifest({
            "model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_material"]},
            "model.retail.order_count": {"sql": ci_environment["sql_target_rev_unsupported"]},
        })

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
defaults:
  ci:
    mode: strict
    fail_on_refusals: true
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
  - metric_id: "metric-cnt-2"
    dbt_model: "model.retail.order_count"
    ci_action: "fail"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 2
        assert res.overall_outcome == "completed_with_material_drift_and_refusals"
        assert res.report["counts"]["material_metrics"] >= 1
        assert res.report["counts"]["refused_metrics"] >= 1
        assert "🔴 **Material Drift & Refusal** (Exit 2)" in res.markdown

    def test_advisory_mode_material_drift_exits_0(self, ci_environment, tmp_path):
        """When ci_action is 'warn', material drift does NOT cause failure -> exits 0."""
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_material"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "warn"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        assert res.exit_code == 0
        assert res.overall_outcome == "completed"


# ---------------------------------------------------------------------------
# 4. Artifact Schema & Secret Redaction
# ---------------------------------------------------------------------------

class TestArtifactSchemaAndSecrets:
    def test_report_schema_and_timings(self, ci_environment, tmp_path):
        """Validate metricguard_ci_report_v1 structure and timing measurements."""
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_clean"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=ci_environment["db_url"],
        )

        rep = res.report
        assert rep["schema"] == CI_REPORT_SCHEMA
        assert rep["version"] == CI_REPORT_VERSION
        assert rep["workspace"] == "retail"
        assert rep["trusted_config_hash"] is not None
        assert "effective_policy" in rep
        assert rep["effective_policy"]["source"] == "store_and_trusted_base_config"

        # Verify timings
        timings = rep["timings"]
        for key in ("config_validation_ms", "manifest_diff_ms", "metric_resolution_ms", "reconciliation_ms", "report_generation_ms", "total_ms"):
            assert key in timings
            assert isinstance(timings[key], (int, float))

    def test_secret_redaction(self):
        """Secret redaction catches passwords, tokens, API keys, and connection strings."""
        dirty_strings = [
            "Connecting to postgresql://admin:super_secret_password123@db.internal:5432/analytics",
            "Authorization: Bearer ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            "Using github_pat_11ABCD1234567890abcdef1234567890",
            "api_key: sk-live-secret-token-key-12345",
        ]
        for s in dirty_strings:
            clean = redact_ci_secrets(s)
            assert "super_secret_password123" not in clean
            assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in clean
            assert "github_pat_11ABCD1234567890abcdef1234567890" not in clean
            assert "sk-live-secret-token-key-12345" not in clean
            assert "[REDACTED" in clean


# ---------------------------------------------------------------------------
# 5. GitHub Adapter & Comment Idempotency
# ---------------------------------------------------------------------------

class TestGitHubAdapterAndIdempotency:
    @patch("urllib.request.urlopen")
    def test_find_existing_comment_id(self, mock_urlopen):
        """Adapter correctly finds existing comment with marker."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps([
            {"id": 101, "body": "LGTM!"},
            {"id": 202, "body": f"Some text\n{PR_COMMENT_MARKER}\nReport content"},
        ]).encode("utf-8")
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        found_id = find_existing_comment_id(
            api_url="https://api.github.com",
            repository="owner/repo",
            pr_number=42,
            token="ghp_test_token",
        )
        assert found_id == 202

    @patch("urllib.request.urlopen")
    def test_post_or_update_pr_comment_creates_new(self, mock_urlopen):
        """When no existing comment exists, adapter POSTs a new comment."""
        # 1. GET returns empty list
        mock_get_resp = MagicMock()
        mock_get_resp.read.return_value = b"[]"
        mock_get_resp.status = 200

        # 2. POST returns 201
        mock_post_resp = MagicMock()
        mock_post_resp.status = 201

        mock_urlopen.return_value.__enter__.side_effect = [mock_get_resp, mock_post_resp]

        success, msg = post_or_update_pr_comment(
            api_url="https://api.github.com",
            repository="owner/repo",
            pr_number=42,
            token="ghp_test_token",
            markdown_content=f"{PR_COMMENT_MARKER}\nNew Report",
        )
        assert success is True
        assert "Created new comment" in msg

    @patch("urllib.request.urlopen")
    def test_post_or_update_pr_comment_updates_existing(self, mock_urlopen):
        """When existing comment is found, adapter PATCHes existing comment ID."""
        # 1. GET returns comment with marker
        mock_get_resp = MagicMock()
        mock_get_resp.read.return_value = json.dumps([
            {"id": 777, "body": f"Old report {PR_COMMENT_MARKER}"}
        ]).encode("utf-8")
        mock_get_resp.status = 200

        # 2. PATCH returns 200
        mock_patch_resp = MagicMock()
        mock_patch_resp.status = 200

        mock_urlopen.return_value.__enter__.side_effect = [mock_get_resp, mock_patch_resp]

        success, msg = post_or_update_pr_comment(
            api_url="https://api.github.com",
            repository="owner/repo",
            pr_number=42,
            token="ghp_test_token",
            markdown_content=f"{PR_COMMENT_MARKER}\nUpdated Report",
        )
        assert success is True
        assert "Updated existing comment #777" in msg

    def test_run_adapter_missing_markdown_exits_3(self, tmp_path):
        """Adapter exits 3 if markdown artifact is missing."""
        code = run_adapter(markdown_path=tmp_path / "does_not_exist.md")
        assert code == 3


# ---------------------------------------------------------------------------
# 6. CLI Execution Tests
# ---------------------------------------------------------------------------

class TestCLIExecutionDiffCI:
    def test_cli_diff_ci_clean_exits_0(self, ci_environment, tmp_path, capsys):
        """CLI: diff-ci with clean manifests exits 0 and prints summary."""
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_clean"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        out_json = tmp_path / "report.json"
        out_md = tmp_path / "summary.md"

        code = run_cli([
            "diff-ci",
            "--config", str(cfg_file),
            "--base-manifest", str(bm_path),
            "--target-manifest", str(tm_path),
            "--output-json", str(out_json),
            "--output-markdown", str(out_md),
            "--database-url", ci_environment["db_url"],
        ])
        captured = capsys.readouterr()
        assert code == 0
        assert out_json.is_file()
        assert out_md.is_file()
        assert "MetricGuard Change Control" in captured.out

    def test_cli_diff_ci_subprocess_invocation(self, ci_environment, tmp_path):
        """Subprocess: Execute `python -m services.cli diff-ci` end-to-end."""
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": ci_environment["sql_target_rev_clean"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{ci_environment['snapshot_id']}"
""", encoding="utf-8")

        cmd = [
            sys.executable, "-m", "services.cli", "diff-ci",
            "--config", str(cfg_file),
            "--base-manifest", str(bm_path),
            "--target-manifest", str(tm_path),
            "--database-url", ci_environment["db_url"],
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["schema"] == CI_REPORT_SCHEMA
        assert data["overall_outcome"] == "completed"
