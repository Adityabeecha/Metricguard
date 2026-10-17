"""MetricGuard V6.3 Turnkey CI / PR Metric Change Gate Core.

This module provides the provider-neutral analytical CI change gate engine:
1. Trusted base config vs candidate config resolution and governance enforcement.
2. Candidate config proposed modification detection (blocking self-weakening).
3. Manifest ingestion (base manifest from target branch, candidate manifest from PR).
4. Governed Metric resolution and immutable reference snapshot execution.
5. Deterministic exit codes and precedence:
   - 0: Clean / advisory completion (no enforcing material drift or refusals)
   - 1: Enforcing material drift
   - 2: Analytical refusal (takes precedence over materiality)
   - 3: Operational / configuration / manifest failure
6. Deterministic artifacts:
   - Machine-readable JSON artifact (schema: metricguard_ci_report_v1)
   - Zero-Green compliant Markdown report for pull-request comments
7. Secret redaction and high-resolution timing profiling.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import uuid4

from packages.metricguard_core.common import CoreError
from packages.metricguard_core.snapshot import inspect_snapshot
from services.api.store import DomainError, Store, fingerprint
from services.config import (
    AppConfig,
    CIConfig,
    ConfigErrorCode,
    ConfigProvenance,
    LoadedConfig,
    MetricBinding,
    MetricGuardConfig,
    ValidationIssue,
    compute_semantic_config_hash,
    load_config_from_file,
    load_config_from_str,
    normalize_and_validate_app_url,
    validate_config,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CI_REPORT_SCHEMA = "metricguard_ci_report_v1"
CI_REPORT_VERSION = 1
CLI_VERSION = "6.3.0"
PR_COMMENT_MARKER = "<!-- metricguard-ci-report -->"

# Comprehensive secret pattern matching API keys, tokens, passwords, and connection strings
_SECRET_PATTERNS = [
    re.compile(r'(?i)(?:api[_-]?(?:key|secret)|secret[_-]?key|password|secret|token)[\s=:]+[\'"]?([^\s\'";]+)[\'"]?'),
    re.compile(r'(?i)(?:authorization)[\s=:]+(?:bearer\s+)?[\'"]?([^\s\'";]+)[\'"]?'),
    re.compile(r'(?i)\bbearer\s+([A-Za-z0-9_\-\.]{12,})'),
    re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{22,}\b'),
    re.compile(r'([a-zA-Z0-9\+\.\-]+://[^:\s@]+:)([^@\s/]+)(@)'),
]


def redact_ci_secrets(text: str) -> str:
    """Mask tokens, passwords, bearer credentials, and DB URIs in strings."""
    if not isinstance(text, str):
        return text

    # Mask database connection strings: postgresql://user:pass@host -> postgresql://user:[REDACTED]@host
    text = re.sub(r'([a-zA-Z0-9\+\.\-]+://[^:\s@]+:)([^@\s/]+)(@)', r'\1[REDACTED]\3', text)

    # Mask known GitHub tokens
    text = re.sub(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b', '[REDACTED_TOKEN]', text)
    text = re.sub(r'\bgithub_pat_[A-Za-z0-9_]{22,}\b', '[REDACTED_TOKEN]', text)

    # Mask general credential assignments
    def _mask_match(m: re.Match) -> str:
        full = m.group(0)
        val = m.group(1)
        if len(val) >= 4:
            return full.replace(val, '[REDACTED]')
        return full

    for pat in _SECRET_PATTERNS[:3]:
        text = pat.sub(_mask_match, text)

    return text


def sanitize_dict_secrets(obj: Any) -> Any:
    """Recursively sanitize strings inside nested dicts/lists."""
    if isinstance(obj, str):
        return redact_ci_secrets(obj)
    elif isinstance(obj, dict):
        return {k: sanitize_dict_secrets(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_dict_secrets(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Configuration Diffing (Candidate vs Trusted Base)
# ---------------------------------------------------------------------------

@dataclass
class ProposedConfigChange:
    category: str
    item: str
    base_value: Any
    candidate_value: Any
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "item": self.item,
            "base_value": self.base_value,
            "candidate_value": self.candidate_value,
            "description": self.description,
        }


def diff_repository_configs(
    base_cfg: MetricGuardConfig,
    candidate_cfg: MetricGuardConfig,
) -> list[ProposedConfigChange]:
    """
    Detect all differences between trusted base configuration and candidate PR configuration.
    Candidate modifications are strictly non-authoritative for the current PR evaluation.
    """
    changes: list[ProposedConfigChange] = []

    # 1. Defaults CI
    if base_cfg.defaults.ci.mode != candidate_cfg.defaults.ci.mode:
        changes.append(ProposedConfigChange(
            category="ci_defaults_modified",
            item="defaults.ci.mode",
            base_value=base_cfg.defaults.ci.mode,
            candidate_value=candidate_cfg.defaults.ci.mode,
            description=f"Candidate proposes changing CI mode from '{base_cfg.defaults.ci.mode}' to '{candidate_cfg.defaults.ci.mode}'.",
        ))

    if base_cfg.defaults.ci.fail_on_refusals != candidate_cfg.defaults.ci.fail_on_refusals:
        changes.append(ProposedConfigChange(
            category="ci_defaults_modified",
            item="defaults.ci.fail_on_refusals",
            base_value=base_cfg.defaults.ci.fail_on_refusals,
            candidate_value=candidate_cfg.defaults.ci.fail_on_refusals,
            description=f"Candidate proposes changing fail_on_refusals from {base_cfg.defaults.ci.fail_on_refusals} to {candidate_cfg.defaults.ci.fail_on_refusals}.",
        ))

    # 2. Metric bindings
    base_metrics = {m.metric_id: m for m in base_cfg.metrics}
    cand_metrics = {m.metric_id: m for m in candidate_cfg.metrics}

    # Bindings removed
    for mid, b_binding in base_metrics.items():
        if mid not in cand_metrics:
            changes.append(ProposedConfigChange(
                category="metric_binding_removed",
                item=f"metrics[{mid}]",
                base_value=b_binding.model_dump(),
                candidate_value=None,
                description=f"Candidate proposes removing metric binding for '{mid}' (bound to '{b_binding.dbt_model}').",
            ))

    # Bindings added
    for mid, c_binding in cand_metrics.items():
        if mid not in base_metrics:
            changes.append(ProposedConfigChange(
                category="metric_binding_added",
                item=f"metrics[{mid}]",
                base_value=None,
                candidate_value=c_binding.model_dump(),
                description=f"Candidate proposes adding metric binding: '{mid}' -> '{c_binding.dbt_model}' (ci_action: {c_binding.ci_action}).",
            ))

    # Bindings modified
    for mid in set(base_metrics.keys()) & set(cand_metrics.keys()):
        b_bind = base_metrics[mid]
        c_bind = cand_metrics[mid]

        if b_bind.ci_action != c_bind.ci_action:
            changes.append(ProposedConfigChange(
                category="metric_binding_modified",
                item=f"metrics[{mid}].ci_action",
                base_value=b_bind.ci_action,
                candidate_value=c_bind.ci_action,
                description=f"Candidate proposes changing CI action from '{b_bind.ci_action}' to '{c_bind.ci_action}' for metric '{mid}'.",
            ))

        if b_bind.dbt_model != c_bind.dbt_model:
            changes.append(ProposedConfigChange(
                category="metric_binding_modified",
                item=f"metrics[{mid}].dbt_model",
                base_value=b_bind.dbt_model,
                candidate_value=c_bind.dbt_model,
                description=f"Candidate proposes changing dbt model target from '{b_bind.dbt_model}' to '{c_bind.dbt_model}' for metric '{mid}'.",
            ))

    # 3. Snapshot binding
    b_snap = base_cfg.snapshot.model_dump() if base_cfg.snapshot else None
    c_snap = candidate_cfg.snapshot.model_dump() if candidate_cfg.snapshot else None
    if b_snap != c_snap:
        changes.append(ProposedConfigChange(
            category="snapshot_modified",
            item="snapshot",
            base_value=b_snap,
            candidate_value=c_snap,
            description="Candidate proposes changing reference snapshot binding.",
        ))

    # 4. App URL
    b_app = base_cfg.app.base_url if (base_cfg.app and base_cfg.app.base_url) else None
    c_app = candidate_cfg.app.base_url if (candidate_cfg.app and candidate_cfg.app.base_url) else None
    if b_app != c_app:
        changes.append(ProposedConfigChange(
            category="app_url_modified",
            item="app.base_url",
            base_value=b_app,
            candidate_value=c_app,
            description=f"Candidate proposes changing app base_url from '{b_app}' to '{c_app}'.",
        ))

    return changes


# ---------------------------------------------------------------------------
# Turnkey CI Execution Engine
# ---------------------------------------------------------------------------

@dataclass
class CIDiffExecutionResult:
    report: dict[str, Any]
    exit_code: int
    overall_outcome: str
    markdown: str
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)


def execute_diff_ci(
    config_path: Path | str,
    base_manifest_path: Path | str,
    target_manifest_path: Path | str,
    base_config_path: Optional[Path | str] = None,
    database_url: Optional[str] = None,
    data_dir: Optional[Path | str] = None,
    provenance: ConfigProvenance = ConfigProvenance.LOCAL_USER,
    materiality_policy: Optional[dict] = None,
) -> CIDiffExecutionResult:
    """
    Execute turnkey, provider-neutral pull-request metric diff CI gate.
    """
    t_start = time.perf_counter()
    run_id = f"diff-ci-{uuid4().hex}"
    issues: list[ValidationIssue] = []

    # -----------------------------------------------------------------------
    # Step 1: Validate manifests exist
    # -----------------------------------------------------------------------
    b_mpath = Path(base_manifest_path)
    t_mpath = Path(target_manifest_path)

    if not b_mpath.is_file():
        issues.append(ValidationIssue(
            code=ConfigErrorCode.INVALID_MANIFEST.value,
            field_path="base_manifest",
            message=f"Base manifest file not found at '{b_mpath}'.",
        ))
    if not t_mpath.is_file():
        issues.append(ValidationIssue(
            code=ConfigErrorCode.INVALID_MANIFEST.value,
            field_path="target_manifest",
            message=f"Target manifest file not found at '{t_mpath}'.",
        ))

    if issues:
        empty_report = _create_operational_error_report(
            run_id=run_id,
            issues=issues,
            t_start=t_start,
        )
        md = format_ci_markdown_report(empty_report)
        return CIDiffExecutionResult(
            report=empty_report,
            exit_code=3,
            overall_outcome="operational_failure",
            markdown=md,
            is_valid=False,
            issues=issues,
        )

    try:
        base_manifest_bytes = b_mpath.read_bytes()
        json.loads(base_manifest_bytes.decode("utf-8"))
    except Exception as exc:
        issues.append(ValidationIssue(
            code=ConfigErrorCode.INVALID_MANIFEST.value,
            field_path="base_manifest",
            message=f"Failed to read/parse base manifest: {exc}",
        ))

    try:
        target_manifest_bytes = t_mpath.read_bytes()
        json.loads(target_manifest_bytes.decode("utf-8"))
    except Exception as exc:
        issues.append(ValidationIssue(
            code=ConfigErrorCode.INVALID_MANIFEST.value,
            field_path="target_manifest",
            message=f"Failed to read/parse target manifest: {exc}",
        ))

    if issues:
        empty_report = _create_operational_error_report(
            run_id=run_id,
            issues=issues,
            t_start=t_start,
        )
        md = format_ci_markdown_report(empty_report)
        return CIDiffExecutionResult(
            report=empty_report,
            exit_code=3,
            overall_outcome="operational_failure",
            markdown=md,
            is_valid=False,
            issues=issues,
        )

    # -----------------------------------------------------------------------
    # Step 2: Load and validate configurations
    # -----------------------------------------------------------------------
    # A. Candidate config (from PR)
    cand_loaded, cand_issues = load_config_from_file(
        config_path,
        provenance=provenance,
    )
    if cand_issues or not cand_loaded:
        empty_report = _create_operational_error_report(
            run_id=run_id,
            issues=cand_issues,
            t_start=t_start,
        )
        md = format_ci_markdown_report(empty_report)
        return CIDiffExecutionResult(
            report=empty_report,
            exit_code=2 if any(i.code != ConfigErrorCode.CONFIG_FILE_NOT_FOUND.value for i in cand_issues) else 3,
            overall_outcome="operational_failure",
            markdown=md,
            is_valid=False,
            issues=cand_issues,
        )

    # B. Trusted Base config (from target/main branch)
    # If base_config_path is not specified, candidate config is assumed as base
    # (e.g. for PRs that did not alter metricguard.yml)
    if base_config_path and Path(base_config_path).is_file():
        base_loaded, base_issues = load_config_from_file(
            base_config_path,
            provenance=ConfigProvenance.TRUSTED_BASE,
        )
        if base_issues or not base_loaded:
            empty_report = _create_operational_error_report(
                run_id=run_id,
                issues=base_issues,
                t_start=t_start,
            )
            md = format_ci_markdown_report(empty_report)
            return CIDiffExecutionResult(
                report=empty_report,
                exit_code=3,
                overall_outcome="operational_failure",
                markdown=md,
                is_valid=False,
                issues=base_issues,
            )
    else:
        # Self-contained or base_config omitted
        base_loaded = cand_loaded

    # Compute proposed configuration changes
    proposed_changes = diff_repository_configs(base_loaded.config, cand_loaded.config)

    # GOVERNANCE RULE: Current evaluation policy is ALWAYS governed by base_loaded config!
    governing_config = base_loaded.config
    trusted_config_hash = base_loaded.config_hash
    candidate_config_hash = cand_loaded.config_hash
    workspace = governing_config.workspace

    t_cfg = time.perf_counter()
    config_validation_ms = round((t_cfg - t_start) * 1000, 2)

    # -----------------------------------------------------------------------
    # Step 3: Resolve Store & Ingest Manifest Revisions
    # -----------------------------------------------------------------------
    db_url = database_url or os.environ.get("METRICGUARD_DATABASE_URL")
    if not db_url:
        dd = Path(data_dir or os.environ.get("METRICGUARD_DATA_DIR", "data"))
        db_url = f"sqlite:///{dd / 'metricguard.db'}"

    try:
        store = Store(db_url)
        # Verify workspace exists
        try:
            store.get_project(workspace)
        except DomainError:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.WORKSPACE_NOT_FOUND.value,
                field_path="workspace",
                message=f"Workspace '{workspace}' not found in MetricGuard Store.",
            ))
            empty_report = _create_operational_error_report(run_id, issues, t_start)
            return CIDiffExecutionResult(
                report=empty_report,
                exit_code=2,
                overall_outcome="operational_failure",
                markdown=format_ci_markdown_report(empty_report),
                is_valid=False,
                issues=issues,
            )

        # Ingest base manifest into Store
        base_rev = store.record_dbt_import(
            base_manifest_bytes,
            project_id=workspace,
            source_revision={"ref": "base", "path": str(b_mpath)},
        )
        # Ingest candidate manifest into Store
        target_rev = store.record_dbt_import(
            target_manifest_bytes,
            project_id=workspace,
            source_revision={"ref": "target", "path": str(t_mpath)},
        )

    except Exception as exc:
        issues.append(ValidationIssue(
            code=ConfigErrorCode.STORE_UNAVAILABLE.value,
            field_path="",
            message=f"Store connection or manifest ingestion failed: {redact_ci_secrets(str(exc))}",
        ))
        empty_report = _create_operational_error_report(run_id, issues, t_start)
        return CIDiffExecutionResult(
            report=empty_report,
            exit_code=3,
            overall_outcome="operational_failure",
            markdown=format_ci_markdown_report(empty_report),
            is_valid=False,
            issues=issues,
        )

    t_man = time.perf_counter()
    manifest_diff_ms = round((t_man - t_cfg) * 1000, 2)

    # -----------------------------------------------------------------------
    # Step 4: Metric & Definition Synchronization for Declared Bindings
    # -----------------------------------------------------------------------
    # Connect declared metric bindings from trusted config to Store
    base_res_by_uid = {r["unique_id"]: r for r in base_rev.get("resources", [])}
    metric_binding_map: dict[str, MetricBinding] = {m.metric_id: m for m in governing_config.metrics}
    effective_metric_policies: list[dict[str, Any]] = []

    for binding in governing_config.metrics:
        mid = binding.metric_id
        dbt_uid = binding.dbt_model

        try:
            m_rec = store.get_metric(mid, project_id=workspace)
        except DomainError:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.METRIC_NOT_FOUND.value,
                field_path=f"metrics[{mid}]",
                message=f"Metric '{mid}' declared in metricguard.yml does not exist in workspace '{workspace}'.",
            ))
            continue

        # Attach external reference if not present
        try:
            store.attach_external_metric_reference(
                metric_id=mid,
                provider="dbt",
                external_id=dbt_uid,
                project_id=workspace,
            )
        except DomainError as de:
            if de.code == "CONFLICT":
                # Already mapped to another metric
                issues.append(ValidationIssue(
                    code=ConfigErrorCode.DUPLICATE_DBT_MODEL_BINDING.value,
                    field_path=f"metrics[{mid}].dbt_model",
                    message=de.message,
                ))
                continue

        # Check if metric has an active definition bound in the Store
        active_b = m_rec.get("active_binding")
        if not active_b:
            bindings = store.list_metric_definition_bindings(metric_id=mid, project_id=workspace)
            active_b = next((b for b in bindings if b.get("status") == "active"), None)

        if not active_b:
            # If no active binding in Store, bind the base manifest's imported definition
            base_r = base_res_by_uid.get(dbt_uid)
            if base_r and base_r.get("definition_id") and base_r.get("definition_version_id"):
                store.bind_metric_definition(
                    metric_id=mid,
                    definition_id=base_r["definition_id"],
                    status="active",
                    approved_version_id=base_r["definition_version_id"],
                    project_id=workspace,
                )

        effective_metric_policies.append({
            "metric_id": mid,
            "display_name": m_rec.get("display_name"),
            "importance": m_rec.get("importance", "standard"),
            "materiality_policy": m_rec.get("materiality_policy") or {},
            "ci_action": binding.ci_action,
            "fail_on_refusals": governing_config.defaults.ci.fail_on_refusals,
        })

    if issues:
        empty_report = _create_operational_error_report(run_id, issues, t_start)
        return CIDiffExecutionResult(
            report=empty_report,
            exit_code=2,
            overall_outcome="operational_failure",
            markdown=format_ci_markdown_report(empty_report),
            is_valid=False,
            issues=issues,
        )

    t_res = time.perf_counter()
    metric_resolution_ms = round((t_res - t_man) * 1000, 2)

    # -----------------------------------------------------------------------
    # Step 5: Reference Snapshot Resolution & Validation
    # -----------------------------------------------------------------------
    resolved_snapshot_id: Optional[str] = None
    snap_binding = governing_config.snapshot

    if snap_binding and snap_binding.snapshot_id:
        resolved_snapshot_id = snap_binding.snapshot_id
    elif snap_binding and snap_binding.file and snap_binding.contract_id:
        s_file = Path(snap_binding.file)
        if not s_file.is_file():
            issues.append(ValidationIssue(
                code=ConfigErrorCode.SNAPSHOT_NOT_FOUND.value,
                field_path="snapshot.file",
                message=f"Snapshot file not found at '{s_file}'.",
            ))
            empty_report = _create_operational_error_report(run_id, issues, t_start)
            return CIDiffExecutionResult(
                report=empty_report,
                exit_code=3,
                overall_outcome="operational_failure",
                markdown=format_ci_markdown_report(empty_report),
                is_valid=False,
                issues=issues,
            )
        try:
            s_manifest = inspect_snapshot(str(s_file))
            rec_snap = store.record_snapshot(
                s_file.stem,
                s_manifest,
                str(s_file),
                project_id=workspace,
            )
            resolved_snapshot_id = rec_snap["id"]
        except Exception as exc:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.SNAPSHOT_NOT_FOUND.value,
                field_path="snapshot.file",
                message=f"Failed to inspect/record snapshot file '{s_file}': {exc}",
            ))
            empty_report = _create_operational_error_report(run_id, issues, t_start)
            return CIDiffExecutionResult(
                report=empty_report,
                exit_code=3,
                overall_outcome="operational_failure",
                markdown=format_ci_markdown_report(empty_report),
                is_valid=False,
                issues=issues,
            )
    else:
        # Default snapshot resolution: take the latest snapshot in the workspace
        snaps = store.list_snapshots(project_id=workspace)
        if snaps:
            resolved_snapshot_id = snaps[0]["id"]
        else:
            issues.append(ValidationIssue(
                code=ConfigErrorCode.SNAPSHOT_NOT_FOUND.value,
                field_path="snapshot",
                message=f"No snapshots available in workspace '{workspace}'. Specify snapshot in metricguard.yml.",
            ))
            empty_report = _create_operational_error_report(run_id, issues, t_start)
            return CIDiffExecutionResult(
                report=empty_report,
                exit_code=3,
                overall_outcome="operational_failure",
                markdown=format_ci_markdown_report(empty_report),
                is_valid=False,
                issues=issues,
            )

    # -----------------------------------------------------------------------
    # Step 6: Reconciliation & Impact Analysis via Deterministic Engine
    # -----------------------------------------------------------------------
    try:
        diff_output = store.execute_metric_diff(
            baseline_revision_id=base_rev["id"],
            target_revision_id=target_rev["id"],
            snapshot_id=resolved_snapshot_id,
            mode=governing_config.defaults.ci.mode,
            materiality_policy=materiality_policy,
            project_id=workspace,
        )
    except DomainError as err:
        exit_code = 2 if err.code in ("UNSUPPORTED_SQL", "UNSUPPORTED_CONSTRUCT", "REFUSAL") else 3
        issues.append(ValidationIssue(
            code=err.code,
            field_path="",
            message=err.message,
        ))
        empty_report = _create_operational_error_report(run_id, issues, t_start)
        return CIDiffExecutionResult(
            report=empty_report,
            exit_code=exit_code,
            overall_outcome="operational_failure",
            markdown=format_ci_markdown_report(empty_report),
            is_valid=False,
            issues=issues,
        )
    except Exception as exc:
        issues.append(ValidationIssue(
            code=ConfigErrorCode.STORE_UNAVAILABLE.value,
            field_path="",
            message=f"Reconciliation error: {exc}",
        ))
        empty_report = _create_operational_error_report(run_id, issues, t_start)
        return CIDiffExecutionResult(
            report=empty_report,
            exit_code=3,
            overall_outcome="operational_failure",
            markdown=format_ci_markdown_report(empty_report),
            is_valid=False,
            issues=issues,
        )

    t_rec = time.perf_counter()
    reconciliation_ms = round((t_rec - t_res) * 1000, 2)

    # -----------------------------------------------------------------------
    # Step 7: Apply CI Policy Consequence & Determine Exit Code
    # -----------------------------------------------------------------------
    evaluated_metrics: list[dict[str, Any]] = []
    enforcing_material_count = 0
    refused_metrics_count = 0
    clean_metrics_count = 0

    fail_on_refusals = governing_config.defaults.ci.fail_on_refusals

    for m in diff_output.get("metrics", []):
        mid = m.get("metric_id")
        binding = metric_binding_map.get(mid)
        ci_action = binding.ci_action if binding else "fail"
        exec_status = m.get("execution_status", "executed")
        mat_info = m.get("materiality") or {}
        is_material = bool(mat_info.get("is_material", False))

        # Outcome determination for this specific metric
        if exec_status == "refused":
            refused_metrics_count += 1
            metric_outcome = "refused"
        elif is_material:
            metric_outcome = "material_drift"
            if ci_action == "fail":
                enforcing_material_count += 1
        else:
            clean_metrics_count += 1
            metric_outcome = "analyzed_clean"

        # V6.4: CI -> Investigation Handoff
        # Resolve durable investigation draft reusing the exact comparison run
        inv_info: dict[str, Any] = {
            "available": False,
            "draft_id": None,
            "path": None,
            "is_refusal": exec_status == "refused",
        }
        try:
            draft = store.resolve_ci_investigation_draft(
                project_id=workspace,
                ci_run_id=run_id,
                metric_id=mid,
                baseline_version_id=m.get("approved_version_id"),
                target_version_id=m.get("target_version_id"),
                snapshot_id=resolved_snapshot_id,
                comparison_run_id=m.get("comparison_run_id"),
                status="refused" if exec_status == "refused" else "draft",
                refusal_reason=m.get("refusal_reason"),
                diagnostics=m.get("diagnostics"),
                ci_provenance={
                    "ci_run_id": run_id,
                    "base_manifest_hash": base_rev.get("manifest_hash") if base_rev else None,
                    "target_manifest_hash": target_rev.get("manifest_hash") if target_rev else None,
                    "trusted_config_hash": trusted_config_hash,
                    "candidate_config_hash": candidate_config_hash,
                    "policy_source": "store_and_trusted_base_config",
                    "ci_action": ci_action,
                },
            )
            inv_info = {
                "available": True,
                "draft_id": draft["id"],
                "path": f"/w/{workspace}/investigations/{draft['id']}",
                "is_refusal": exec_status == "refused",
            }
        except Exception:
            pass

        evaluated_metrics.append({
            "metric_id": mid,
            "display_name": m.get("display_name"),
            "importance": m.get("importance", "standard"),
            "dbt_model": binding.dbt_model if binding else m.get("resource_id"),
            "approved_version_id": m.get("approved_version_id"),
            "target_version_id": m.get("target_version_id"),
            "baseline_value": m.get("baseline_value"),
            "target_value": m.get("target_value"),
            "exact_delta": m.get("exact_delta"),
            "absolute_delta": m.get("absolute_delta"),
            "relative_change_percent": m.get("relative_change_percent"),
            "is_material": is_material,
            "materiality": mat_info,
            "ci_action": ci_action,
            "execution_status": exec_status,
            "outcome": metric_outcome,
            "refusal_reason": m.get("refusal_reason"),
            "diagnostics": m.get("diagnostics"),
            "comparison_run_id": m.get("comparison_run_id"),
            "investigation": inv_info,
        })

    # Overall outcome and Exit Code determination
    # Strict Precedence: 3 (operational) > 2 (refusal if fail_on_refusals) > 1 (enforcing material) > 0 (clean/advisory)
    if refused_metrics_count > 0 and enforcing_material_count > 0:
        overall_outcome = "completed_with_material_drift_and_refusals"
        exit_code = 2 if fail_on_refusals else 1
    elif refused_metrics_count > 0:
        overall_outcome = "completed_with_refusals"
        exit_code = 2 if fail_on_refusals else 0
    elif enforcing_material_count > 0:
        overall_outcome = "completed_with_material_drift"
        exit_code = 1
    else:
        overall_outcome = "completed"
        exit_code = 0

    # -----------------------------------------------------------------------
    # Step 8: App URL Resolution with Trust Hierarchy
    # -----------------------------------------------------------------------
    env_app_url = os.environ.get("METRICGUARD_APP_URL")
    effective_app_url = None
    effective_app_url_trusted = False

    if env_app_url and env_app_url.strip():
        try:
            effective_app_url = normalize_and_validate_app_url(env_app_url.strip(), "METRICGUARD_APP_URL")
            effective_app_url_trusted = True
        except ValueError:
            pass
    elif base_config_path and base_loaded.config.app and base_loaded.config.app.base_url:
        effective_app_url = base_loaded.config.app.base_url
        effective_app_url_trusted = True
    elif cand_loaded.config.app and cand_loaded.config.app.base_url:
        effective_app_url = cand_loaded.config.app.base_url
        effective_app_url_trusted = cand_loaded.effective_app_url_trusted

    t_rep_start = time.perf_counter()

    # -----------------------------------------------------------------------
    # Step 9: Assemble Final JSON Report (metricguard_ci_report_v1)
    # -----------------------------------------------------------------------
    report_dict: dict[str, Any] = {
        "schema": CI_REPORT_SCHEMA,
        "version": CI_REPORT_VERSION,
        "run_id": run_id,
        "workspace": workspace,
        "overall_outcome": overall_outcome,
        "exit_code": exit_code,
        "base_revision_id": base_rev["id"],
        "target_revision_id": target_rev["id"],
        "base_manifest_hash": base_rev["manifest_hash"],
        "target_manifest_hash": target_rev["manifest_hash"],
        "trusted_config_hash": trusted_config_hash,
        "candidate_config_hash": candidate_config_hash,
        "snapshot_id": resolved_snapshot_id,
        "snapshot_hash": diff_output.get("snapshot_hash"),
        "effective_policy": {
            "source": "store_and_trusted_base_config",
            "trusted_config_hash": trusted_config_hash,
            "candidate_config_hash": candidate_config_hash,
            "mode": governing_config.defaults.ci.mode,
            "fail_on_refusals": governing_config.defaults.ci.fail_on_refusals,
            "metrics": effective_metric_policies,
        },
        "proposed_config_changes": [c.to_dict() for c in proposed_changes],
        "metrics": evaluated_metrics,
        "counts": {
            "total_changed_resources": diff_output["counts"]["total_changed_resources"],
            "unchanged_resources": diff_output["counts"]["unchanged_resources"],
            "total_declared_metrics_affected": len(evaluated_metrics),
            "successfully_analyzed_metrics": clean_metrics_count + (len(evaluated_metrics) - clean_metrics_count - refused_metrics_count),
            "refused_metrics": refused_metrics_count,
            "undeclared_changed_resources": diff_output["counts"]["undeclared_changed_resources"],
            "material_metrics": sum(1 for m in evaluated_metrics if m["is_material"]),
            "enforcing_material_metrics": enforcing_material_count,
        },
        "coverage": diff_output.get("coverage", {}),
        "app_url": effective_app_url,
        "app_url_trusted": effective_app_url_trusted,
        "cli_version": CLI_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    t_end = time.perf_counter()
    report_generation_ms = round((t_end - t_rep_start) * 1000, 2)
    total_ms = round((t_end - t_start) * 1000, 2)

    report_dict["timings"] = {
        "config_validation_ms": config_validation_ms,
        "manifest_diff_ms": manifest_diff_ms,
        "metric_resolution_ms": metric_resolution_ms,
        "reconciliation_ms": reconciliation_ms,
        "report_generation_ms": report_generation_ms,
        "total_ms": total_ms,
    }

    # Redact any secrets throughout the entire data structure
    sanitized_report = sanitize_dict_secrets(report_dict)

    # Generate Zero-Green Markdown report
    markdown_report = format_ci_markdown_report(sanitized_report)

    return CIDiffExecutionResult(
        report=sanitized_report,
        exit_code=exit_code,
        overall_outcome=overall_outcome,
        markdown=markdown_report,
        is_valid=True,
        issues=[],
    )


def _create_operational_error_report(
    run_id: str,
    issues: list[ValidationIssue],
    t_start: float,
) -> dict[str, Any]:
    """Build an operational failure report artifact."""
    total_ms = round((time.perf_counter() - t_start) * 1000, 2)
    return {
        "schema": CI_REPORT_SCHEMA,
        "version": CI_REPORT_VERSION,
        "run_id": run_id,
        "workspace": "unknown",
        "overall_outcome": "operational_failure",
        "exit_code": 3,
        "base_revision_id": None,
        "target_revision_id": None,
        "base_manifest_hash": None,
        "target_manifest_hash": None,
        "trusted_config_hash": None,
        "candidate_config_hash": None,
        "snapshot_id": None,
        "snapshot_hash": None,
        "effective_policy": {},
        "proposed_config_changes": [],
        "metrics": [],
        "counts": {
            "total_changed_resources": 0,
            "unchanged_resources": 0,
            "total_declared_metrics_affected": 0,
            "successfully_analyzed_metrics": 0,
            "refused_metrics": 0,
            "undeclared_changed_resources": 0,
            "material_metrics": 0,
            "enforcing_material_metrics": 0,
        },
        "coverage": {},
        "issues": [i.to_dict() for i in issues],
        "timings": {"total_ms": total_ms},
        "app_url": None,
        "app_url_trusted": False,
        "cli_version": CLI_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Zero-Green Deterministic Markdown Generator
# ---------------------------------------------------------------------------

def format_ci_markdown_report(report: dict[str, Any]) -> str:
    """
    Format a concise, task-oriented PR comment report adhering strictly
    to the MetricGuard Zero-Green design system:
    - 🔵 Analyzed / Pass
    - 🔴 Material Drift
    - 🟠 Refused / Warning
    - ⚪ Neutral / Unchanged
    """
    lines: list[str] = []

    # Comment markers for idempotent GitHub PR updating
    lines.append(PR_COMMENT_MARKER)
    ws = report.get("workspace", "default")
    lines.append(f"<!-- metricguard:workspace:{ws} -->")
    lines.append("")

    lines.append("### MetricGuard Change Control")
    lines.append("")

    overall = report.get("overall_outcome", "completed")
    exit_code = report.get("exit_code", 0)

    # Outcome banner
    if overall == "completed":
        lines.append("🔵 **Analyzed — Clean** (Exit 0)")
    elif overall == "completed_with_material_drift":
        lines.append(f"🔴 **Material Drift Exceeded** (Exit {exit_code})")
    elif overall == "completed_with_refusals":
        lines.append(f"🟠 **Analytical Refusal** (Exit {exit_code})")
    elif overall == "completed_with_material_drift_and_refusals":
        lines.append(f"🔴 **Material Drift & Refusal** (Exit {exit_code})")
    else:
        lines.append(f"❌ **Operational Failure** (Exit {exit_code})")

    lines.append("")

    # Summary bullet points
    counts = report.get("counts", {})
    aff_count = counts.get("total_declared_metrics_affected", 0)
    mat_count = counts.get("material_metrics", 0)
    ref_count = counts.get("refused_metrics", 0)
    analyzed_count = max(0, aff_count - ref_count - mat_count)

    lines.append(f"• **{aff_count}** Declared Metrics affected")
    if mat_count > 0:
        lines.append(f"• **{mat_count}** Material changes (threshold exceeded)")
    if ref_count > 0:
        lines.append(f"• **{ref_count}** Refused (unsupported analytical construct)")
    if analyzed_count > 0:
        lines.append(f"• **{analyzed_count}** Within threshold / clean")
    lines.append("")

    # Operational issues (if any)
    issues = report.get("issues", [])
    if issues:
        lines.append("#### ❌ Operational Errors")
        lines.append("")
        for iss in issues:
            loc = f" at `{iss['field_path']}`" if iss.get("field_path") else ""
            lines.append(f"- **[{iss['code']}]**{loc}: {iss['message']}")
            if iss.get("suggested_correction"):
                lines.append(f"  *Suggested: {iss['suggested_correction']}*")
        lines.append("")
        return "\n".join(lines)

    # Compact Metric Table
    metrics = report.get("metrics", [])
    if metrics:
        lines.append("| Metric | Importance | Approved | Candidate | Delta | Materiality | Action | Outcome |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

        for m in metrics:
            m_name = m.get("display_name") or m.get("metric_id")
            m_id = m.get("metric_id")
            imp = m.get("importance", "standard")
            b_val = _fmt_val(m.get("baseline_value"))
            t_val = _fmt_val(m.get("target_value"))
            delta_str = _fmt_delta(m.get("exact_delta"), m.get("relative_change_percent"))
            ci_act = m.get("ci_action", "fail")
            is_mat = m.get("is_material", False)
            status = m.get("execution_status", "executed")

            if status == "refused":
                mat_badge = "n/a"
                outcome_badge = "🟠 Refused"
            elif is_mat:
                mat_badge = "🔴 Material"
                outcome_badge = "🔴 Material" if ci_act == "fail" else "🟠 Warning"
            else:
                mat_badge = "⚪ Pass"
                outcome_badge = "🔵 Analyzed"

            lines.append(f"| **{m_name}** (`{m_id}`) | `{imp}` | {b_val} | {t_val} | {delta_str} | {mat_badge} | `{ci_act}` | {outcome_badge} |")

        lines.append("")

    app_url_trusted = bool(report.get("app_url_trusted"))
    app_url = report.get("app_url")
    base_app_url = app_url.rstrip("/") if (app_url_trusted and app_url) else None

    # Detailed Sections
    # 1. Material Changes
    material_metrics = [m for m in metrics if m.get("is_material")]
    if material_metrics:
        lines.append("#### 🔴 Material Changes")
        lines.append("")
        for m in material_metrics:
            m_name = m.get("display_name") or m.get("metric_id")
            mat = m.get("materiality") or {}
            abs_delta = m.get("absolute_delta", "0")
            rel_delta = m.get("relative_change_percent")
            rel_str = ""
            if rel_delta is not None:
                try:
                    rel_f = float(str(rel_delta).rstrip("%"))
                    rel_str = f" ({rel_f:.2f}%)"
                except (ValueError, TypeError):
                    rel_str = f" ({rel_delta})"
            rule = mat.get("exceeded_threshold_rule", "threshold")
            run_id_ref = m.get("comparison_run_id") or "pinned"
            inv = m.get("investigation") or {}
            inv_path = inv.get("path")
            inv_draft_id = inv.get("draft_id")

            lines.append(f"- **{m_name}**: Delta of `{abs_delta}`{rel_str} exceeds governed `{rule}`.")
            if base_app_url and inv_path:
                lines.append(f"  *Evidence Reference: `{run_id_ref}` — [🔬 Open Investigation]({base_app_url}{inv_path})*")
            elif inv_draft_id:
                lines.append(f"  *Evidence Reference: `{run_id_ref}` (Draft: `{inv_draft_id}`)*")
            else:
                lines.append(f"  *Evidence Reference: `{run_id_ref}`*")
        lines.append("")

    # 2. Analytical Refusals
    refused_metrics = [m for m in metrics if m.get("execution_status") == "refused"]
    if refused_metrics:
        lines.append("#### 🟠 Analytical Refusals")
        lines.append("")
        for m in refused_metrics:
            m_name = m.get("display_name") or m.get("metric_id")
            reason = m.get("refusal_reason") or "Analytical construct not supported."
            inv = m.get("investigation") or {}
            inv_path = inv.get("path")
            inv_draft_id = inv.get("draft_id")

            if base_app_url and inv_path:
                lines.append(f"- **{m_name}**: {reason} — [⚠️ Review Refusal]({base_app_url}{inv_path})")
            elif inv_draft_id:
                lines.append(f"- **{m_name}**: {reason} (Draft: `{inv_draft_id}`)")
            else:
                lines.append(f"- **{m_name}**: {reason}")
        lines.append("")

    # 3. Proposed Configuration Changes (from Candidate PR)
    proposed = report.get("proposed_config_changes", [])
    if proposed:
        lines.append("#### ⚙️ Proposed Repository Configuration Changes")
        lines.append("")
        lines.append("> ⚠️ **Governance Notice**: The candidate pull request proposes changes to `metricguard.yml`. "
                     "In accordance with MetricGuard Model B governance, the current evaluation is enforced strictly "
                     "using the trusted base policy. Proposed changes become active only after review and merge to main.")
        lines.append("")
        lines.append("| Item | Base Policy | Proposed Candidate | Details |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for p in proposed:
            b_val = str(p.get("base_value")) if p.get("base_value") is not None else "*none*"
            c_val = str(p.get("candidate_value")) if p.get("candidate_value") is not None else "*removed*"
            lines.append(f"| `{p['item']}` | `{b_val}` | `{c_val}` | {p['description']} |")
        lines.append("")

    # 4. Resource Coverage
    lines.append("#### 📊 Resource Coverage")
    lines.append("")
    lines.append(f"- **Changed dbt resources**: {counts.get('total_changed_resources', 0)}")
    lines.append(f"- **Bound declared metrics**: {aff_count}")
    lines.append(f"- **Undeclared changed resources**: {counts.get('undeclared_changed_resources', 0)}")
    lines.append("")

    # 5. Governance & Policy Metadata
    lines.append("#### 🔒 Governance & Metadata")
    lines.append("")
    lines.append(f"- **Workspace**: `{report.get('workspace')}`")
    lines.append(f"- **Snapshot ID**: `{report.get('snapshot_id')}`")
    lines.append(f"- **Trusted Base Config Hash**: `{report.get('trusted_config_hash')}`")
    if report.get("candidate_config_hash"):
        lines.append(f"- **Candidate Config Hash**: `{report.get('candidate_config_hash')}`")
    timings = report.get("timings", {})
    if timings.get("total_ms"):
        lines.append(f"- **CI Reconciliation Duration**: `{timings['total_ms']} ms`")
    lines.append("")

    # Studio Deep Link CTA (strictly trusted only, omitted in headless mode)
    if report.get("app_url_trusted") and report.get("app_url"):
        base_app_url = report["app_url"].rstrip("/")
        cta_url = f"{base_app_url}/w/{ws}/changes"
        lines.append(f"[🔍 View in MetricGuard Studio]({cta_url})")
        lines.append("")

    return "\n".join(lines)


def _fmt_val(val: Any) -> str:
    if val is None:
        return "n/a"
    try:
        dec = Decimal(str(val))
        return f"{dec:,.2f}"
    except Exception:
        return str(val)


def _fmt_delta(exact: Any, rel_pct: Any) -> str:
    if exact is None:
        return "n/a"
    try:
        dec = Decimal(str(exact))
        sign = "+" if dec > 0 else ""
        pct_part = ""
        if rel_pct is not None:
            try:
                rel_f = float(str(rel_pct).rstrip("%"))
                pct_part = f" ({rel_f:+.2f}%)"
            except (ValueError, TypeError):
                pct_part = f" ({rel_pct})"
        return f"{sign}{dec:,.2f}{pct_part}"
    except Exception:
        return str(exact)
