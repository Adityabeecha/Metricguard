"""MetricGuard V6.2 Declarative Repository Configuration Test Suite.

Comprehensive tests covering:
- Model B Source of Truth (governance in Store, repository CI bindings in YAML)
- Test Matrix Points A through S:
  A: Valid minimal config
  B: Malformed YAML (MALFORMED_YAML)
  C: Unsupported config version (UNSUPPORTED_CONFIG_VERSION)
  D: Missing workspace (MISSING_REQUIRED_FIELD)
  E: Valid Metric binding
  F: Metric ID missing in Store (METRIC_NOT_FOUND)
  G: Metric belongs to another workspace (CROSS_WORKSPACE_METRIC)
  H: dbt model missing from manifest (DBT_RESOURCE_NOT_FOUND)
  I: Duplicate Metric ID (DUPLICATE_METRIC_BINDING)
  J: Duplicate dbt model binding (DUPLICATE_DBT_MODEL_BINDING)
  K: Invalid ci_action (INVALID_CI_ACTION)
  L: Valid app URL
  M: Malformed app URL (INVALID_APP_URL)
  N: No app URL (valid, deep links omitted)
  O: Schema-only validation without Store
  P: Governed validation with unavailable Store (STORE_UNAVAILABLE)
  Q: Deterministic semantic config hash
  R: YAML key-order, comment, and whitespace invariance
  S: Forbidden governance fields rejected (FORBIDDEN_GOVERNANCE_FIELD)
- Provenance & Trust Boundary (trusted base vs untrusted candidate config vs env override)
- Store Immutability during validation
- CLI execution and exit-code semantics (0, 2, 3) across format options
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from services.api.store import Store, metrics as metrics_tbl
from services.cli import run_cli
from services.config import (
    ConfigErrorCode,
    ConfigProvenance,
    LoadedConfig,
    MetricGuardConfig,
    ValidationIssue,
    ValidationResult,
    canonicalize_config_dict,
    compute_semantic_config_hash,
    load_config_from_file,
    load_config_from_str,
    normalize_and_validate_app_url,
    validate_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store_env(tmp_path):
    """Create two isolated workspaces in a clean SQLite store."""
    db_path = tmp_path / "store.db"
    db_url = f"sqlite:///{db_path}"
    store = Store(db_url)

    # Workspace 1: retail
    store.create_project(name="Retail Analytics", project_id="retail")
    m1 = store.create_metric(display_name="Gross Revenue", metric_id="metric-rev-1", project_id="retail")
    m2 = store.create_metric(display_name="Order Count", metric_id="metric-cnt-2", project_id="retail")

    # Workspace 2: finance
    store.create_project(name="Finance Workspace", project_id="finance")
    m_fin = store.create_metric(display_name="Net Margin", metric_id="metric-fin-9", project_id="finance")

    # Record snapshot in retail
    dummy_manifest = {
        "manifest_hash": "hash-retail-snap-1",
        "row_count": 100,
        "database_hash": "db-hash-1",
    }
    snap = store.record_snapshot("Retail Golden Snapshot", dummy_manifest, str(tmp_path / "dummy.duckdb"), project_id="retail")

    return {
        "store": store,
        "db_url": db_url,
        "db_path": db_path,
        "retail_snapshot_id": snap["id"],
        "metric_rev_1": m1["id"],
        "metric_cnt_2": m2["id"],
        "metric_fin_9": m_fin["id"],
    }


@pytest.fixture
def dbt_manifest(tmp_path):
    """Create a sample dbt manifest.json."""
    manifest_data = {
        "metadata": {"dbt_version": "1.8.0"},
        "nodes": {
            "model.retail.fct_orders": {
                "unique_id": "model.retail.fct_orders",
                "name": "fct_orders",
                "resource_type": "model",
                "original_file_path": "models/fct_orders.sql",
            },
            "model.retail.dim_customers": {
                "unique_id": "model.retail.dim_customers",
                "name": "dim_customers",
                "resource_type": "model",
                "original_file_path": "models/dim_customers.sql",
            },
        },
    }
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest_data), encoding="utf-8")
    return mpath


# ---------------------------------------------------------------------------
# Core Schema & Parsing Tests (Points A - E, K - N, S)
# ---------------------------------------------------------------------------

class TestConfigParsingAndSchema:
    def test_point_a_valid_minimal_config(self):
        """A: Valid minimal config loads with default CI strict mode and fail_on_refusals."""
        yaml_content = """
version: 1
workspace: retail
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert len(issues) == 0
        assert loaded is not None
        assert loaded.config.version == 1
        assert loaded.config.workspace == "retail"
        assert loaded.config.defaults.ci.mode == "strict"
        assert loaded.config.defaults.ci.fail_on_refusals is True
        assert len(loaded.config.metrics) == 0
        assert loaded.config.snapshot is None
        assert loaded.config.app is None

    def test_point_b_malformed_yaml(self):
        """B: Malformed YAML triggers MALFORMED_YAML error code."""
        bad_yaml = """
version: 1
workspace: [unclosed_list
"""
        loaded, issues = load_config_from_str(bad_yaml)
        assert loaded is None
        assert len(issues) >= 1
        assert issues[0].code == ConfigErrorCode.MALFORMED_YAML.value

    def test_point_b_non_dict_yaml(self):
        """B: Non-mapping YAML triggers MALFORMED_YAML error code."""
        loaded, issues = load_config_from_str("- a\n- b\n- c\n")
        assert loaded is None
        assert any(i.code == ConfigErrorCode.MALFORMED_YAML.value for i in issues)

    def test_point_c_unsupported_config_version(self):
        """C: Unsupported config version triggers UNSUPPORTED_CONFIG_VERSION."""
        for v in (2, 0, 99):
            yaml_content = f"""
version: {v}
workspace: retail
"""
            loaded, issues = load_config_from_str(yaml_content)
            assert loaded is None
            assert any(i.code == ConfigErrorCode.UNSUPPORTED_CONFIG_VERSION.value for i in issues)

    def test_point_d_missing_workspace(self):
        """D: Missing workspace triggers MISSING_REQUIRED_FIELD."""
        yaml_content = """
version: 1
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert loaded is None
        assert any(i.code == ConfigErrorCode.MISSING_REQUIRED_FIELD.value for i in issues)

    def test_point_e_valid_metric_bindings(self):
        """E: Valid metric bindings parse correctly."""
        yaml_content = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
  - metric_id: "metric-cnt-2"
    dbt_model: "model.retail.dim_customers"
    ci_action: "warn"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert len(issues) == 0
        assert loaded is not None
        assert len(loaded.config.metrics) == 2
        assert loaded.config.metrics[0].metric_id == "metric-rev-1"
        assert loaded.config.metrics[0].ci_action == "fail"
        assert loaded.config.metrics[1].ci_action == "warn"

    def test_point_i_duplicate_metric_id(self):
        """I: Duplicate metric_id in bindings triggers DUPLICATE_METRIC_BINDING."""
        yaml_content = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders_v2"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert loaded is None
        assert any(i.code == ConfigErrorCode.DUPLICATE_METRIC_BINDING.value for i in issues)

    def test_point_j_duplicate_dbt_model_binding(self):
        """J: Duplicate dbt_model binding triggers DUPLICATE_DBT_MODEL_BINDING."""
        yaml_content = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
  - metric_id: "metric-cnt-2"
    dbt_model: "model.retail.fct_orders"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert loaded is None
        assert any(i.code == ConfigErrorCode.DUPLICATE_DBT_MODEL_BINDING.value for i in issues)

    def test_point_k_invalid_ci_action(self):
        """K: Invalid ci_action triggers INVALID_CI_ACTION."""
        yaml_content = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    ci_action: "block_pr"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert loaded is None
        assert any(i.code == ConfigErrorCode.INVALID_CI_ACTION.value for i in issues)

    def test_point_l_valid_app_url(self):
        """L: Valid app URL normalizes properly (stripping trailing slash)."""
        yaml_content = """
version: 1
workspace: retail
app:
  base_url: "https://metricguard.corp.internal/"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert len(issues) == 0
        assert loaded is not None
        assert loaded.config.app.base_url == "https://metricguard.corp.internal"
        assert loaded.effective_app_url == "https://metricguard.corp.internal"

    def test_point_m_malformed_app_url(self):
        """M: Malformed app URLs trigger INVALID_APP_URL."""
        bad_urls = [
            "ftp://metricguard.corp.internal",
            "https://metricguard.corp.internal?tab=orders",
            "https://metricguard.corp.internal#section",
            "not-a-url",
        ]
        for url in bad_urls:
            yaml_content = f"""
version: 1
workspace: retail
app:
  base_url: "{url}"
"""
            loaded, issues = load_config_from_str(yaml_content)
            assert loaded is None
            assert any(i.code == ConfigErrorCode.INVALID_APP_URL.value for i in issues), f"Failed for {url}"

    def test_point_n_no_app_url(self):
        """N: Config without app URL is completely valid, leaving effective_app_url as None."""
        yaml_content = """
version: 1
workspace: retail
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert len(issues) == 0
        assert loaded.effective_app_url is None

    def test_point_s_forbidden_governance_fields_rejected(self):
        """S: YAML cannot declare governance fields owned by Store (Model B)."""
        forbidden_snippets = [
            # In metric binding
            """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    display_name: "My Custom Metric Name"
""",
            """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    importance: "critical"
""",
            """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    thresholds:
      absolute: 10.0
""",
            """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
    lifecycle_state: "active"
""",
            # Top-level governance declaration
            """
version: 1
workspace: retail
governance:
  reviewers: ["alice"]
""",
            """
version: 1
workspace: retail
thresholds:
  default_materiality: 0.05
""",
        ]
        for yml in forbidden_snippets:
            loaded, issues = load_config_from_str(yml)
            assert loaded is None, f"Expected failure for: {yml}"
            assert any(i.code == ConfigErrorCode.FORBIDDEN_GOVERNANCE_FIELD.value for i in issues)


# ---------------------------------------------------------------------------
# Semantic Hashing & Invariance Tests (Points Q, R)
# ---------------------------------------------------------------------------

class TestSemanticHashing:
    def test_point_q_semantic_config_hash_format(self):
        """Q: Config hash is a deterministic 64-character SHA-256 string."""
        yaml_content = """
version: 1
workspace: retail
metrics:
  - metric_id: "metric-rev-1"
    dbt_model: "model.retail.fct_orders"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert loaded is not None
        assert len(loaded.config_hash) == 64
        assert int(loaded.config_hash, 16) > 0

    def test_point_r_yaml_invariance(self):
        """R: Key reordering, formatting variations, and comments produce IDENTICAL semantic hash."""
        yaml_a = """
# Production MetricGuard Configuration
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
    dbt_model: "model.retail.dim_customers"
    ci_action: "warn"
"""
        yaml_b = """
# Completely different comments and key ordering
metrics:
  # Notice metric-cnt-2 comes first here!
  - ci_action: "warn"
    dbt_model: "model.retail.dim_customers"
    metric_id: "metric-cnt-2"
  - dbt_model: "model.retail.fct_orders"
    ci_action: "fail"
    metric_id: "metric-rev-1"
workspace: retail
version: 1
defaults:
  ci:
    fail_on_refusals: true
    mode: strict
"""
        loaded_a, _ = load_config_from_str(yaml_a)
        loaded_b, _ = load_config_from_str(yaml_b)

        assert loaded_a is not None
        assert loaded_b is not None
        assert loaded_a.config_hash == loaded_b.config_hash

    def test_semantic_hash_changes_on_actual_semantic_modification(self):
        """Semantic hash differs when actual configuration semantics change."""
        yaml_a = """
version: 1
workspace: retail
defaults:
  ci:
    mode: strict
"""
        yaml_b = """
version: 1
workspace: retail
defaults:
  ci:
    mode: advisory
"""
        loaded_a, _ = load_config_from_str(yaml_a)
        loaded_b, _ = load_config_from_str(yaml_b)
        assert loaded_a.config_hash != loaded_b.config_hash


# ---------------------------------------------------------------------------
# Governed Store Validation Tests (Points F, G, O, P)
# ---------------------------------------------------------------------------

class TestGovernedValidation:
    def test_point_o_schema_only_validation_without_store(self, tmp_path):
        """O: Schema-only validation succeeds without any store connection."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: nonexistent_workspace_123
metrics:
  - metric_id: "metric-xyz"
    dbt_model: "model.pkg.tbl"
""", encoding="utf-8")

        res = validate_config(config_source=cfg_file, schema_only=True)
        assert res.is_valid is True
        assert res.mode == "schema_only"
        assert res.store_connected is False

    def test_governed_validation_valid(self, store_env, tmp_path):
        """Governed validation succeeds when workspace and metrics exist in Store."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "{store_env['metric_rev_1']}"
    dbt_model: "model.retail.fct_orders"
snapshot:
  snapshot_id: "{store_env['retail_snapshot_id']}"
""", encoding="utf-8")

        res = validate_config(
            config_source=cfg_file,
            schema_only=False,
            database_url=store_env["db_url"],
        )
        assert res.is_valid is True
        assert res.mode == "governed"
        assert res.store_connected is True
        assert res.metrics_verified == 1

    def test_point_f_metric_id_missing_in_store(self, store_env, tmp_path):
        """F: Metric ID missing in Store triggers METRIC_NOT_FOUND."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-nonexistent-999"
    dbt_model: "model.retail.fct_orders"
""", encoding="utf-8")

        res = validate_config(
            config_source=cfg_file,
            schema_only=False,
            database_url=store_env["db_url"],
        )
        assert res.is_valid is False
        assert any(i.code == ConfigErrorCode.METRIC_NOT_FOUND.value for i in res.issues)

    def test_point_g_cross_workspace_metric(self, store_env, tmp_path):
        """G: Metric belonging to another workspace triggers CROSS_WORKSPACE_METRIC."""
        # metric_fin_9 belongs to workspace 'finance', not 'retail'
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "{store_env['metric_fin_9']}"
    dbt_model: "model.retail.fct_orders"
""", encoding="utf-8")

        res = validate_config(
            config_source=cfg_file,
            schema_only=False,
            database_url=store_env["db_url"],
        )
        assert res.is_valid is False
        cross_issues = [i for i in res.issues if i.code == ConfigErrorCode.CROSS_WORKSPACE_METRIC.value]
        assert len(cross_issues) == 1
        assert "finance" in cross_issues[0].message

    def test_workspace_not_found(self, store_env, tmp_path):
        """Nonexistent workspace triggers WORKSPACE_NOT_FOUND in governed mode."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: non_existent_ws
""", encoding="utf-8")

        res = validate_config(
            config_source=cfg_file,
            schema_only=False,
            database_url=store_env["db_url"],
        )
        assert res.is_valid is False
        assert any(i.code == ConfigErrorCode.WORKSPACE_NOT_FOUND.value for i in res.issues)

    def test_snapshot_not_found_in_workspace(self, store_env, tmp_path):
        """Snapshot not found in workspace triggers SNAPSHOT_NOT_FOUND."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
snapshot:
  snapshot_id: "snap-does-not-exist"
""", encoding="utf-8")

        res = validate_config(
            config_source=cfg_file,
            schema_only=False,
            database_url=store_env["db_url"],
        )
        assert res.is_valid is False
        assert any(i.code == ConfigErrorCode.SNAPSHOT_NOT_FOUND.value for i in res.issues)

    def test_point_p_store_unavailable(self, tmp_path):
        """P: Governed validation with unavailable store returns STORE_UNAVAILABLE."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
""", encoding="utf-8")

        # Invalid database url pointing to unconnectable postgres
        res = validate_config(
            config_source=cfg_file,
            schema_only=False,
            database_url="postgresql://nonexistent:nonexistent@localhost:59999/dummy",
        )
        assert res.is_valid is False
        assert any(i.code == ConfigErrorCode.STORE_UNAVAILABLE.value for i in res.issues)


# ---------------------------------------------------------------------------
# dbt Manifest Validation Tests (Point H)
# ---------------------------------------------------------------------------

class TestManifestValidation:
    def test_point_h_manifest_matching(self, dbt_manifest, tmp_path):
        """H: Existing dbt resource validates; missing dbt resource triggers DBT_RESOURCE_NOT_FOUND."""
        valid_yml = tmp_path / "valid.yml"
        valid_yml.write_text("""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-1"
    dbt_model: "model.retail.fct_orders"
""", encoding="utf-8")

        res = validate_config(config_source=valid_yml, manifest_path=dbt_manifest, schema_only=True)
        assert res.is_valid is True
        assert res.dbt_resources_verified == 1

        missing_yml = tmp_path / "missing.yml"
        missing_yml.write_text("""
version: 1
workspace: retail
metrics:
  - metric_id: "metric-1"
    dbt_model: "model.retail.fct_unknown_model"
""", encoding="utf-8")

        res_missing = validate_config(config_source=missing_yml, manifest_path=dbt_manifest, schema_only=True)
        assert res_missing.is_valid is False
        assert any(i.code == ConfigErrorCode.DBT_RESOURCE_NOT_FOUND.value for i in res_missing.issues)

    def test_manifest_file_not_found(self, tmp_path):
        """Missing manifest path triggers INVALID_MANIFEST."""
        yml = tmp_path / "valid.yml"
        yml.write_text("version: 1\nworkspace: retail\n", encoding="utf-8")
        res = validate_config(config_source=yml, manifest_path=tmp_path / "does_not_exist.json", schema_only=True)
        assert res.is_valid is False
        assert any(i.code == ConfigErrorCode.INVALID_MANIFEST.value for i in res.issues)


# ---------------------------------------------------------------------------
# Provenance & Trust Boundary Tests
# ---------------------------------------------------------------------------

class TestProvenanceAndTrustBoundary:
    def test_candidate_config_url_is_untrusted(self):
        """Candidate PR config cannot introduce trusted app URL for deep links."""
        yaml_content = """
version: 1
workspace: retail
app:
  base_url: "https://evil-phishing-metricguard.com"
"""
        loaded, issues = load_config_from_str(
            yaml_content,
            provenance=ConfigProvenance.UNTRUSTED_CANDIDATE,
        )
        assert loaded is not None
        assert loaded.effective_app_url == "https://evil-phishing-metricguard.com"
        assert loaded.effective_app_url_trusted is False

    def test_trusted_base_config_url_is_trusted(self):
        """Trusted base config URL has effective_app_url_trusted == True."""
        yaml_content = """
version: 1
workspace: retail
app:
  base_url: "https://metricguard.internal.corp"
"""
        loaded, issues = load_config_from_str(
            yaml_content,
            provenance=ConfigProvenance.TRUSTED_BASE,
        )
        assert loaded is not None
        assert loaded.effective_app_url == "https://metricguard.internal.corp"
        assert loaded.effective_app_url_trusted is True

    def test_env_app_url_overrides_candidate_config(self, monkeypatch):
        """Environment variable METRICGUARD_APP_URL overrides config and is trusted."""
        monkeypatch.setenv("METRICGUARD_APP_URL", "https://official.metricguard.internal")
        yaml_content = """
version: 1
workspace: retail
app:
  base_url: "https://candidate-suggested.com"
"""
        loaded, issues = load_config_from_str(
            yaml_content,
            provenance=ConfigProvenance.UNTRUSTED_CANDIDATE,
        )
        assert loaded is not None
        assert loaded.effective_app_url == "https://official.metricguard.internal"
        assert loaded.effective_app_url_trusted is True


# ---------------------------------------------------------------------------
# Store Immutability Tests
# ---------------------------------------------------------------------------

class TestStoreImmutability:
    def test_governed_validation_is_read_only(self, store_env, tmp_path):
        """Validation must not mutate or insert records in the Store."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "{store_env['metric_rev_1']}"
    dbt_model: "model.retail.fct_orders"
""", encoding="utf-8")

        # Snapshot metric table row count
        with store_env["store"].engine.connect() as conn:
            before_count = conn.execute(select(metrics_tbl)).all()

        validate_config(
            config_source=cfg_file,
            schema_only=False,
            database_url=store_env["db_url"],
        )

        with store_env["store"].engine.connect() as conn:
            after_count = conn.execute(select(metrics_tbl)).all()

        assert len(before_count) == len(after_count)


# ---------------------------------------------------------------------------
# CLI Execution Tests
# ---------------------------------------------------------------------------

class TestCLIExecution:
    def test_cli_validate_config_schema_only_valid_exits_0(self, tmp_path, capsys):
        """CLI: Valid config with --schema-only exits 0."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
""", encoding="utf-8")

        code = run_cli(["validate-config", "--config", str(cfg_file), "--schema-only"])
        captured = capsys.readouterr()
        assert code == 0
        assert "MetricGuard config validated successfully" in captured.out
        assert "retail" in captured.out

    def test_cli_validate_config_governed_valid_exits_0(self, store_env, tmp_path, capsys):
        """CLI: Governed validation with live store exits 0."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text(f"""
version: 1
workspace: retail
metrics:
  - metric_id: "{store_env['metric_rev_1']}"
    dbt_model: "model.retail.fct_orders"
""", encoding="utf-8")

        code = run_cli([
            "validate-config",
            "--config", str(cfg_file),
            "--database-url", store_env["db_url"],
        ])
        captured = capsys.readouterr()
        assert code == 0
        assert "MetricGuard config validated successfully" in captured.out
        assert "Metrics verified:        1" in captured.out

    def test_cli_validate_config_domain_failure_exits_2(self, tmp_path, capsys):
        """CLI: Validation error (e.g. malformed YAML, unsupported version) exits 2."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 99
workspace: retail
""", encoding="utf-8")

        code = run_cli(["validate-config", "--config", str(cfg_file), "--schema-only"])
        captured = capsys.readouterr()
        assert code == 2
        assert "UNSUPPORTED_CONFIG_VERSION" in captured.err

    def test_cli_validate_config_missing_file_exits_3(self, tmp_path, capsys):
        """CLI: Missing config file is an operational failure, exits 3."""
        missing_file = tmp_path / "does_not_exist.yml"
        code = run_cli(["validate-config", "--config", str(missing_file), "--schema-only"])
        captured = capsys.readouterr()
        assert code == 3
        assert "CONFIG_FILE_NOT_FOUND" in captured.err

    def test_cli_validate_config_json_format(self, tmp_path, capsys):
        """CLI: --json flag emits valid JSON."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
""", encoding="utf-8")

        code = run_cli(["validate-config", "--config", str(cfg_file), "--schema-only", "--json"])
        captured = capsys.readouterr()
        assert code == 0
        parsed = json.loads(captured.out)
        assert parsed["is_valid"] is True
        assert parsed["workspace"] == "retail"
        assert parsed["mode"] == "schema_only"

    def test_cli_validate_config_output_file(self, tmp_path):
        """CLI: --output writes machine-readable report to file."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
""", encoding="utf-8")
        out_file = tmp_path / "out_report.json"

        code = run_cli([
            "validate-config",
            "--config", str(cfg_file),
            "--schema-only",
            "--output", str(out_file),
            "--quiet",
        ])
        assert code == 0
        assert out_file.is_file()
        parsed = json.loads(out_file.read_text(encoding="utf-8"))
        assert parsed["is_valid"] is True

    def test_cli_subprocess_invocation(self, tmp_path):
        """CLI: Subprocess execution via `python -m services.cli validate-config`."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
""", encoding="utf-8")

        cmd = [
            sys.executable, "-m", "services.cli", "validate-config",
            "--config", str(cfg_file),
            "--schema-only",
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data["is_valid"] is True

    def test_cli_alias_validate_underscore_config(self, tmp_path):
        """CLI: validate_config alias works identically to validate-config."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("version: 1\nworkspace: retail\n", encoding="utf-8")
        code = run_cli(["validate_config", "--config", str(cfg_file), "--schema-only", "--quiet"])
        assert code == 0

    def test_cli_quiet_flag_suppresses_stdout(self, tmp_path, capsys):
        """CLI: --quiet suppresses all summary stdout."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("version: 1\nworkspace: retail\n", encoding="utf-8")
        code = run_cli(["validate-config", "--config", str(cfg_file), "--schema-only", "-q"])
        captured = capsys.readouterr()
        assert code == 0
        assert captured.out == ""

    def test_cli_provenance_candidate_flag(self, tmp_path, capsys):
        """CLI: --provenance untrusted_candidate marks effective_app_url as untrusted in JSON output."""
        cfg_file = tmp_path / "metricguard.yml"
        cfg_file.write_text("""
version: 1
workspace: retail
app:
  base_url: "https://candidate-pr-url.com"
""", encoding="utf-8")
        code = run_cli([
            "validate-config",
            "--config", str(cfg_file),
            "--schema-only",
            "--provenance", "untrusted_candidate",
            "--json",
        ])
        captured = capsys.readouterr()
        assert code == 0


class TestSnapshotBindings:
    def test_snapshot_file_and_contract(self):
        """Valid snapshot binding with file and contract_id."""
        yaml_content = """
version: 1
workspace: retail
snapshot:
  file: "data/snapshots/golden.duckdb"
  contract_id: "contract-retail-v1"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert len(issues) == 0
        assert loaded.config.snapshot.file == "data/snapshots/golden.duckdb"
        assert loaded.config.snapshot.contract_id == "contract-retail-v1"

    def test_snapshot_mutually_exclusive_error(self):
        """Specifying both snapshot_id and file is rejected."""
        yaml_content = """
version: 1
workspace: retail
snapshot:
  snapshot_id: "snap-123"
  file: "data/snapshots/golden.duckdb"
  contract_id: "contract-1"
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert loaded is None
        assert len(issues) >= 1

    def test_snapshot_missing_strategy_error(self):
        """Specifying neither snapshot_id nor file/contract_id is rejected."""
        yaml_content = """
version: 1
workspace: retail
snapshot: {}
"""
        loaded, issues = load_config_from_str(yaml_content)
        assert loaded is None
        assert len(issues) >= 1

