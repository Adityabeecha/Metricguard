"""Comprehensive test suite for MetricGuard V6.4:
CI -> Investigation Handoff + Pilot Hardening.

Covers:
1. Zero Recomputation & Exact Comparison Run Reuse:
   - CI produces a comparison_run_id and immutable evidence artifact.
   - Investigation draft directly binds this comparison_run_id.
   - Investigation detail API / Studio consumes the existing run without re-running SQL.
2. Evidence Artifact Hash Invariance:
   - Exact evidence hash, counts, and row evidence match between CI and Investigation.
3. Idempotent Handoff Resolution:
   - Repeated CI runs on the same diff/commit resolve to the existing draft.
   - No duplicate drafts created in the workspace.
   - Human investigations with status='completed' or custom notes are NEVER overwritten.
4. Metric Finding Isolation:
   - Distinct affected metrics produce distinct investigation contexts (1 metric -> 1 draft).
5. Analytical Refusal Handoff:
   - Refused metrics generate refusal drafts with refusal_reason and diagnostics.
   - Zero fake quantitative numbers (values and deltas are None).
6. Stale / Missing Artifact Graceful Handling:
   - If referenced comparison run or snapshot is missing, API returns 200 with run=None, not 500.
7. Cross-Workspace Isolation:
   - Workspace B cannot access or view Workspace A's draft or evidence (403/404).
8. Headless vs Trusted Deep Link Resolution:
   - Headless CI omits HTTP links and provides plain text references without broken links.
   - Trusted App URL generates verified [🔬 Open Investigation] and [⚠️ Review Refusal] action links.
9. Pilot Ergonomics:
   - Top-level `metricguard.py` CLI invocation and local DB support.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from packages.metricguard_core import ingest_csv
from services.api.app import create_app
from services.api.store import DomainError, Store, fingerprint
from services.ci_workflow import (
    CI_REPORT_SCHEMA,
    execute_diff_ci,
)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CSV = ROOT / "MetricGuard_Planning_Pack" / "examples" / "orders_golden.csv"
PERIOD_CLAUSE = "order_date >= DATE '2026-08-01' AND order_date < DATE '2026-09-01'"


def _build_manifest(models: dict, version: int = 12) -> dict:
    compiled_key = "compiled_code" if version >= 9 else "compiled_sql"
    nodes = {}
    for uid, info in models.items():
        nodes[uid] = {
            "unique_id": uid,
            "resource_type": "model",
            "name": uid.split(".")[-1],
            "database": "retail",
            "schema": "analytics",
            "alias": uid.split(".")[-1],
            "raw_code": info.get("raw_sql", info.get("sql", "")),
            compiled_key: info["sql"],
            "language": "sql",
            "config": {"materialized": "table"},
            "depends_on": {"nodes": []},
            "description": info.get("description", ""),
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
def v64_environment(tmp_path: Path):
    db_path = tmp_path / "store.db"
    db_url = f"sqlite:///{db_path}"
    store = Store(db_url)
    pid = "retail"
    store.create_project(name="Retail Analytics", project_id=pid)

    # Ingest golden snapshot
    snap_dest = tmp_path / "orders_golden.duckdb"
    snap_manifest = ingest_csv(GOLDEN_CSV, snap_dest)
    snapshot = store.record_snapshot("orders_golden", snap_manifest, str(snap_dest), project_id=pid)

    # Governed Metric 1: Gross Revenue
    store.create_metric(
        display_name="Gross Revenue",
        importance="critical",
        metric_id="metric-rev-1",
        project_id=pid,
    )

    # Governed Metric 2: Order Count
    store.create_metric(
        display_name="Order Count",
        importance="important",
        metric_id="metric-cnt-2",
        project_id=pid,
    )

    # SQL queries
    sql_base_rev = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target_rev_material = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND customer_id = 'C01' AND {PERIOD_CLAUSE}"
    sql_target_rev_clean = f"SELECT SUM(order_amount) FROM orders WHERE status = 'Completed' AND status != 'Cancelled' AND {PERIOD_CLAUSE}"
    sql_target_rev_unsupported = f"SELECT SUM(order_amount) FROM orders JOIN orders b USING (order_id) WHERE {PERIOD_CLAUSE}"

    sql_base_cnt = f"SELECT COUNT(*) FROM orders WHERE status = 'Completed' AND {PERIOD_CLAUSE}"
    sql_target_cnt_clean = f"SELECT COUNT(*) FROM orders WHERE status = 'Completed' AND status != 'Cancelled' AND {PERIOD_CLAUSE}"

    # Setup User & API Client
    u_analyst, key_analyst = store.create_user("Analyst User", "analyst@corp.com")
    store.add_project_member(pid, u_analyst["id"], "analyst")

    app = create_app(data_dir=tmp_path, database_url=store)
    client = TestClient(app)

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
        "client": client,
        "key_analyst": key_analyst,
    }


class TestZeroRecomputationHandoff:
    def test_ci_to_investigation_exact_run_reuse(self, v64_environment, tmp_path):
        """V6.4-P0: CI findings create drafts reusing exact comparison_run_id with ZERO recomputation."""
        env = v64_environment
        store: Store = env["store"]

        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_material"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_content = f"""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{env['snapshot_id']}"
"""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(cfg_content, encoding="utf-8")

        # 1. Execute CI
        result = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )

        assert result.exit_code == 1  # Material drift exceeded
        rep = result.report
        m_finding = rep["metrics"][0]
        assert m_finding["metric_id"] == "metric-rev-1"
        ci_run_id = m_finding["comparison_run_id"]
        assert ci_run_id is not None

        # 2. Check investigation handoff metadata in JSON report
        inv_meta = m_finding["investigation"]
        assert inv_meta["available"] is True
        assert inv_meta["is_refusal"] is False
        draft_id = inv_meta["draft_id"]
        assert draft_id.startswith("draft-ci-")
        assert inv_meta["path"] == f"/w/retail/investigations/{draft_id}"

        # 3. Retrieve draft from Store
        draft = store.get_investigation_draft(draft_id, project_id="retail")
        assert draft["comparison_run_id"] == ci_run_id
        assert draft["snapshot_id"] == env["snapshot_id"]
        assert draft["metric_id"] == "metric-rev-1"
        assert draft["origin"] == "ci"
        assert draft["ci_run_id"] == rep["run_id"]
        assert draft["status"] == "draft"

        # 4. Zero Recomputation Proof via API
        client = env["client"]
        res = client.get(
            f"/api/v1/investigations/{draft_id}",
            headers={"x-api-key": env["key_analyst"], "x-project-id": "retail"},
        )
        assert res.status_code == 200
        detail = res.json()
        assert detail["is_draft"] is True
        assert detail["run_id"] == ci_run_id
        assert detail["run"] is not None
        assert detail["run"]["id"] == ci_run_id
        assert detail["run"]["result"]["delta_target_minus_baseline"] == m_finding["exact_delta"]
        assert detail["run"]["result"]["baseline_value"] == m_finding["baseline_value"]
        assert detail["run"]["result"]["target_value"] == m_finding["target_value"]

    def test_evidence_artifact_hash_invariance(self, v64_environment, tmp_path):
        """V6.4-P0: Evidence hash and row evidence are identical between CI and Investigation."""
        env = v64_environment
        store: Store = env["store"]

        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_material"]}})

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
  snapshot_id: "{env['snapshot_id']}"
""", encoding="utf-8")

        res_ci = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )
        m_finding = res_ci.report["metrics"][0]
        draft_id = m_finding["investigation"]["draft_id"]
        run_id = m_finding["comparison_run_id"]

        # Fetch run directly
        run = store.get_run(run_id)
        ci_evidence = run["result"]["evidence"]
        ci_evidence_hash = fingerprint(ci_evidence)

        # Fetch investigation detail via API
        client = env["client"]
        res = client.get(
            f"/api/v1/investigations/{draft_id}",
            headers={"x-api-key": env["key_analyst"], "x-project-id": "retail"},
        )
        assert res.status_code == 200
        inv_data = res.json()

        # Invariance assertion
        inv_evidence = inv_data["run"]["result"]["evidence"]
        inv_evidence_hash = fingerprint(inv_evidence)
        assert inv_evidence_hash == ci_evidence_hash
        assert len(inv_evidence) == len(ci_evidence)
        assert inv_evidence == ci_evidence


class TestIdempotencyAndWorkPreservation:
    def test_repeated_ci_runs_do_not_duplicate_drafts(self, v64_environment, tmp_path):
        """Repeated CI runs on identical commits/runs resolve to the same draft ID."""
        env = v64_environment
        store: Store = env["store"]

        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_material"]}})

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
  snapshot_id: "{env['snapshot_id']}"
""", encoding="utf-8")

        # Run 1
        res1 = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )
        draft1 = res1.report["metrics"][0]["investigation"]["draft_id"]

        # Run 2 with same manifests and config
        res2 = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )
        draft2 = res2.report["metrics"][0]["investigation"]["draft_id"]

        # Identical draft ID resolved
        assert draft1 == draft2

        # Check total drafts in workspace
        drafts = store.list_investigation_drafts(project_id="retail")
        matching = [d for d in drafts if d["id"] == draft1]
        assert len(matching) == 1

    def test_human_investigation_is_never_overwritten(self, v64_environment, tmp_path):
        """If a human analyst updates notes or records a decision, CI does not overwrite it."""
        env = v64_environment
        store: Store = env["store"]

        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_material"]}})

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
  snapshot_id: "{env['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )
        draft_id = res.report["metrics"][0]["investigation"]["draft_id"]

        # Human analyst edits the draft
        store.update_investigation_draft(
            draft_id=draft_id,
            updates={
                "title": "Analyst Review of Q3 Shift",
                "notes": "Verified intentional change for enterprise accounts.",
                "status": "completed",
            },
            project_id="retail",
        )

        # Subsequent CI run occurs
        res_subsequent = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )
        draft_id_after = res_subsequent.report["metrics"][0]["investigation"]["draft_id"]
        assert draft_id_after == draft_id

        # Verify human modifications were NOT overwritten
        post_draft = store.get_investigation_draft(draft_id, project_id="retail")
        assert post_draft["title"] == "Analyst Review of Q3 Shift"
        assert post_draft["notes"] == "Verified intentional change for enterprise accounts."
        assert post_draft["status"] == "completed"


class TestMultiMetricIsolation:
    def test_each_metric_has_distinct_investigation_context(self, v64_environment, tmp_path):
        """1 Metric Finding -> 1 distinct Investigation Context (no mixed buckets)."""
        env = v64_environment
        store: Store = env["store"]

        base_m = _build_manifest({
            "model.retail.fct_orders": {"sql": env["sql_base_rev"]},
            "model.retail.fct_counts": {"sql": env["sql_base_cnt"]},
        })
        target_m = _build_manifest({
            "model.retail.fct_orders": {"sql": env["sql_target_rev_material"]},
            "model.retail.fct_counts": {"sql": env["sql_target_cnt_clean"]},
        })

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
  - metric_id: "metric-cnt-2"
    dbt_model: "model.retail.fct_counts"
snapshot:
  snapshot_id: "{env['snapshot_id']}"
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )

        assert len(res.report["metrics"]) == 2
        m_by_id = {m["metric_id"]: m for m in res.report["metrics"]}
        assert "metric-rev-1" in m_by_id
        assert "metric-cnt-2" in m_by_id

        m1 = m_by_id["metric-rev-1"]
        m2 = m_by_id["metric-cnt-2"]

        inv1 = m1["investigation"]
        inv2 = m2["investigation"]

        assert inv1["draft_id"] != inv2["draft_id"]
        assert inv1["path"] != inv2["path"]

        draft1 = store.get_investigation_draft(inv1["draft_id"], project_id="retail")
        draft2 = store.get_investigation_draft(inv2["draft_id"], project_id="retail")

        assert draft1["metric_id"] == "metric-rev-1"
        assert draft2["metric_id"] == "metric-cnt-2"
        assert draft1["comparison_run_id"] == m1["comparison_run_id"]
        assert draft2["comparison_run_id"] == m2["comparison_run_id"]


class TestRefusalHandoff:
    def test_refusal_metric_handoff_has_zero_fake_numbers(self, v64_environment, tmp_path):
        """Refusal drafts preserve refusal_reason and provide zero fake quantitative numbers."""
        env = v64_environment
        store: Store = env["store"]

        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_unsupported"]}})

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
  snapshot_id: "{env['snapshot_id']}"
defaults:
  ci:
    fail_on_refusals: false
""", encoding="utf-8")

        res = execute_diff_ci(
            config_path=cfg_file,
            base_manifest_path=bm_path,
            target_manifest_path=tm_path,
            database_url=env["db_url"],
            data_dir=tmp_path,
        )

        m_finding = res.report["metrics"][0]
        assert m_finding["execution_status"] == "refused"
        assert m_finding["baseline_value"] is None
        assert m_finding["target_value"] is None
        assert m_finding["exact_delta"] is None

        inv_meta = m_finding["investigation"]
        assert inv_meta["available"] is True
        assert inv_meta["is_refusal"] is True
        draft_id = inv_meta["draft_id"]
        assert "refusal" in draft_id

        draft = store.get_investigation_draft(draft_id, project_id="retail")
        assert draft["status"] == "refused"
        assert draft["comparison_run_id"] is None
        assert draft["refusal_reason"] is not None

        # API resolution does not fail or produce fake numbers
        client = env["client"]
        api_res = client.get(
            f"/api/v1/investigations/{draft_id}",
            headers={"x-api-key": env["key_analyst"], "x-project-id": "retail"},
        )
        assert api_res.status_code == 200
        data = api_res.json()
        assert data["status"] == "refused"
        assert data["run"] is None


class TestStaleArtifactSafety:
    def test_deleted_or_missing_run_returns_clean_detail(self, v64_environment):
        """API degrades gracefully with run: None if referenced comparison run is missing."""
        env = v64_environment
        store: Store = env["store"]

        # Create draft referencing a non-existent comparison run
        draft = store.record_investigation_draft({
            "title": "Stale Artifact Test",
            "metric_id": "metric-rev-1",
            "comparison_run_id": "non-existent-run-id-999",
            "snapshot_id": env["snapshot_id"],
        }, project_id="retail")

        client = env["client"]
        res = client.get(
            f"/api/v1/investigations/{draft['id']}",
            headers={"x-api-key": env["key_analyst"], "x-project-id": "retail"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == draft["id"]
        assert data["run"] is None
        assert data["is_draft"] is True


class TestWorkspaceIsolation:
    def test_workspace_isolation_blocks_cross_workspace_draft_access(self, v64_environment):
        """Workspace B cannot access Workspace A's investigation draft."""
        env = v64_environment
        store: Store = env["store"]

        # Create Workspace B
        store.create_project(name="Finance Ops", project_id="finance")
        u_fin, key_fin = store.create_user("Finance Analyst", "finance@corp.com")
        store.add_project_member("finance", u_fin["id"], "analyst")

        # Create draft in Workspace A
        draft_a = store.record_investigation_draft({
            "title": "Retail Confidential Investigation",
            "metric_id": "metric-rev-1",
            "snapshot_id": env["snapshot_id"],
        }, project_id="retail")

        # 1. Direct store call with mismatched project_id raises DomainError 403
        with pytest.raises(DomainError) as exc_info:
            store.get_investigation_draft(draft_a["id"], project_id="finance")
        assert exc_info.value.status == 403
        assert exc_info.value.code == "FORBIDDEN"

        # 2. HTTP Client for finance user cannot access draft_a
        client = env["client"]
        res = client.get(
            f"/api/v1/investigations/{draft_a['id']}",
            headers={"x-api-key": key_fin, "x-project-id": "finance"},
        )
        assert res.status_code == 403


class TestMarkdownDeepLinksAndHeadless:
    def test_trusted_app_url_renders_action_links(self, v64_environment, tmp_path):
        """When app_url_trusted is True, Markdown renders verified [Open Investigation] links."""
        env = v64_environment
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_material"]}})

        bm_path = tmp_path / "base_manifest.json"
        tm_path = tmp_path / "target_manifest.json"
        bm_path.write_text(json.dumps(base_m), encoding="utf-8")
        tm_path.write_text(json.dumps(target_m), encoding="utf-8")

        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
app:
  base_url: "https://metricguard.internal.corp"
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{env['snapshot_id']}"
""", encoding="utf-8")

        with patch.dict(os.environ, {"METRICGUARD_APP_URL": "https://metricguard.internal.corp"}):
            res = execute_diff_ci(
                config_path=cfg_file,
                base_manifest_path=bm_path,
                target_manifest_path=tm_path,
                database_url=env["db_url"],
                data_dir=tmp_path,
            )

        assert res.report["app_url_trusted"] is True
        assert "[🔬 Open Investigation](https://metricguard.internal.corp/w/retail/investigations/draft-ci-" in res.markdown

    def test_headless_mode_omits_http_links(self, v64_environment, tmp_path):
        """In headless mode, Markdown omits HTTP links and renders plain draft IDs."""
        env = v64_environment
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_material"]}})

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
  snapshot_id: "{env['snapshot_id']}"
""", encoding="utf-8")

        with patch.dict(os.environ, {}, clear=True):
            res = execute_diff_ci(
                config_path=cfg_file,
                base_manifest_path=bm_path,
                target_manifest_path=tm_path,
                database_url=env["db_url"],
                data_dir=tmp_path,
            )

        assert res.report["app_url_trusted"] is False
        assert "http://" not in res.markdown
        assert "https://" not in res.markdown
        assert "(Draft: `draft-ci-" in res.markdown


class TestPilotErgonomics:
    def test_top_level_metricguard_shim_execution(self, v64_environment, tmp_path):
        """Top-level metricguard.py CLI executes diff-ci seamlessly."""
        env = v64_environment
        base_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_base_rev"]}})
        target_m = _build_manifest({"model.retail.fct_orders": {"sql": env["sql_target_rev_clean"]}})

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
  snapshot_id: "{env['snapshot_id']}"
""", encoding="utf-8")

        cmd = [
            sys.executable, "metricguard.py", "diff-ci",
            "--config", str(cfg_file),
            "--base-manifest", str(bm_path),
            "--target-manifest", str(tm_path),
            "--database-url", env["db_url"],
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["schema"] == CI_REPORT_SCHEMA
        assert data["overall_outcome"] == "completed"
        assert data["metrics"][0]["investigation"]["available"] is True
