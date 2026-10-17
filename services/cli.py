"""V4-03 Headless CLI / CI Automation Workflow – correctness-critical core.

This module provides the deterministic comparison execution contract,
result model, exit-code semantics, and versioned report schema used by
non-interactive CI callers.  It reuses the existing MetricGuard comparison
engine, V3 materiality evaluation, and V3 authentication/RBAC – it does
NOT introduce a separate calculation engine or security model.

Exit codes
----------
0  Supported analysis completed (advisory drift still exits 0).
1  Supported analysis completed in enforcing mode AND exceeded materiality.
2  MetricGuard refused quantitative analysis (unsupported SQL, metadata_only,
   incompatible contract/mapping, missing compiled SQL, etc.).
3  Operational failure (snapshot missing, corrupted artifact, auth failure,
   service error, required input unavailable).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from packages.metricguard_core.common import CoreError, digest, file_hash
from services.api.monitoring import evaluate_materiality
from services.api.store import DomainError, Store, fingerprint

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_SCHEMA = 'metricguard_ci_report_v1'
REPORT_VERSION = 1
CLI_VERSION = '4.3.0'

# Credential redaction pattern – matches API keys, bearer tokens, passwords
_CREDENTIAL_RE = re.compile(
    r'(?i)'
    r'(?:'
    r'(?:api[_-]?(?:key|secret)|secret[_-]?key|password|secret|token)[\s=:]+\S+'
    r'|'
    r'(?:authorization)[\s=:]+(?:bearer\s+)?\S+'
    r'|'
    r'bearer\s+\S+'
    r')',
)


# ---------------------------------------------------------------------------
# Outcome model – these are NOT the same thing
# ---------------------------------------------------------------------------

class CIOutcome(str, Enum):
    """Distinguish supported analysis, refused analysis, and operational failure."""
    SUPPORTED = 'supported'
    REFUSED = 'refused'
    FAILED = 'failed'


# ---------------------------------------------------------------------------
# Exit-code determination – the ONE place where exit logic lives
# ---------------------------------------------------------------------------

def determine_exit_code(
    outcome: CIOutcome,
    mode: str,
    is_material: bool,
) -> int:
    """Map (outcome, mode, materiality) to a deterministic exit code.

    0 = supported analysis completed (advisory drift still 0)
    1 = enforcing mode, supported analysis, material drift
    2 = MetricGuard refused quantitative analysis
    3 = operational failure
    """
    if outcome == CIOutcome.FAILED:
        return 3
    if outcome == CIOutcome.REFUSED:
        return 2
    # outcome == CIOutcome.SUPPORTED
    if mode == 'enforcing' and is_material:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------

def redact_api_key(text: str) -> str:
    """Mask anything that looks like a credential in a string."""
    if not isinstance(text, str):
        return text
    # Mask database URIs with passwords: postgresql://user:pass@host
    text = re.sub(r'([a-zA-Z0-9\+\.\-]+://[^:\s]+:)([^@\s]+)(@)', r'\1[REDACTED]\3', text)
    return _CREDENTIAL_RE.sub('[REDACTED]', text)



def safe_env_api_key() -> str | None:
    """Read METRICGUARD_API_KEY from the environment.  Never prints it."""
    return os.environ.get('METRICGUARD_API_KEY') or None


# ---------------------------------------------------------------------------
# Definition and snapshot resolution
# ---------------------------------------------------------------------------

@dataclass
class ResolvedInputs:
    """Immutable resolved comparison inputs – deterministic comparison identity."""
    project_id: str
    baseline_version: dict
    target_version: dict
    snapshot: dict
    context: dict
    mode: str                          # 'advisory' | 'enforcing'
    materiality_policy: dict | None    # absolute_threshold / relative_threshold
    relationship: str = 'candidate'
    # dbt provenance (when available)
    baseline_dbt_revision: dict | None = None
    target_dbt_revision: dict | None = None
    # Deterministic request fingerprint
    request_hash: str = ''

    def __post_init__(self):
        if not self.request_hash:
            self.request_hash = _compute_request_hash(self)


def _compute_request_hash(inputs: ResolvedInputs) -> str:
    """Deterministic identity from the immutable inputs only.

    Operational metadata (timestamps, mode) is excluded: the hash identifies
    the *analytical comparison* uniquely, not the CI invocation.
    """
    payload = {
        'project_id': inputs.project_id,
        'baseline_version_id': inputs.baseline_version['id'],
        'target_version_id': inputs.target_version['id'],
        'snapshot_id': inputs.snapshot['id'],
        'snapshot_manifest_hash': inputs.snapshot.get('manifest_hash'),
        'context': inputs.context,
    }
    return fingerprint(payload)


def resolve_version(store: Store, selector: str, project_id: str) -> dict:
    """Resolve a definition version selector to exactly one immutable version.

    Accepted selectors:
    - A version UUID (looked up directly), optionally prefixed with ``version:``
    - A dbt unique_id in the form ``dbt:<unique_id>`` – resolves to the
      *latest* linked DefinitionVersion for that unique_id.  If the dbt
      resource is ``metadata_only`` or has no linked version, this is a
      refusal.

    Ambiguous or missing selectors raise DomainError.
    """
    if not selector or not selector.strip():
        raise DomainError('INVALID_INPUT', 'Definition version selector cannot be empty.', 400)
    selector = selector.strip()
    if selector.startswith('dbt:'):
        return _resolve_dbt_version(store, selector[4:], project_id)
    if selector.startswith('version:'):
        selector = selector[8:]
    version = store.version(selector)
    if version.get('project_id', store.project_id) != project_id:
        raise DomainError('FORBIDDEN', 'Definition does not belong to the authorized project.', 403)
    return version


def _resolve_dbt_version(store: Store, unique_id: str, project_id: str) -> dict:
    """Resolve a dbt unique_id to the most recent linked DefinitionVersion."""
    history = store.get_dbt_resource_history(unique_id, project_id=project_id)
    if not history:
        raise DomainError('NOT_FOUND', f"No dbt resource found for unique_id '{unique_id}'.", 404)

    # Find the most recent resource revision that has a linked version
    for rev in history:  # already ordered newest-first
        if rev.get('definition_version_id'):
            version = store.version(rev['definition_version_id'])
            # Attach dbt provenance
            version['_dbt_resource_revision'] = rev
            if rev.get('import_revision_id'):
                try:
                    imp = store.get_dbt_import_revision(rev['import_revision_id'], project_id=project_id)
                    version['_dbt_import_revision'] = imp
                except Exception:
                    pass
            return version

    # All revisions are metadata_only or unlinked
    latest = history[0]
    raise DomainError(
        'UNSUPPORTED_CONSTRUCT',
        f"dbt resource '{unique_id}' is {latest.get('support_state', 'metadata_only')}: "
        + '; '.join(latest.get('diagnostics') or ['no linked definition version']),
    )


def resolve_snapshot(store: Store, selector: str, project_id: str) -> dict:
    """Resolve a snapshot selector to exactly one immutable snapshot.

    Accepted selectors:
    - A snapshot UUID (looked up directly), optionally prefixed with ``snapshot:``

    Ambiguous or missing selectors raise DomainError.
    """
    if not selector or not selector.strip():
        raise DomainError('INVALID_INPUT', 'Snapshot selector cannot be empty.', 400)
    selector = selector.strip()
    if selector.startswith('snapshot:'):
        selector = selector[9:]
    snapshot = store.snapshot(selector)
    if snapshot.get('project_id', store.project_id) != project_id:
        raise DomainError('FORBIDDEN', 'Snapshot does not belong to the authorized project.', 403)
    return snapshot



# ---------------------------------------------------------------------------
# Support-state gating (exit 2 boundary)
# ---------------------------------------------------------------------------

# Domain error codes that map to exit 2 (refused) vs exit 3 (operational)
_REFUSAL_CODES = frozenset({
    'UNSUPPORTED_CONSTRUCT', 'NOT_COMPARABLE', 'CONTEXT_REQUIRED',
    'POLICY_MISMATCH', 'PERIOD_CONFLICT', 'UNSUPPORTED_SQL',
    'MAPPING_CHANGED',
})


def _check_support_state(version: dict, label: str) -> tuple[bool, str | None]:
    """Check if a definition version is eligible for quantitative analysis.

    Returns (is_supported, refusal_diagnostic).
    """
    state = version.get('support_state', 'executable')
    if state == 'executable':
        return True, None

    diagnostics = []
    if state == 'metadata_only':
        diagnostics.append(f'{label} is metadata_only and cannot be quantitatively compared')
        reason = version.get('support_reason')
        if reason:
            diagnostics.append(reason)
        # Include dbt diagnostics if available
        dbt_rev = version.get('_dbt_resource_revision')
        if dbt_rev and dbt_rev.get('diagnostics'):
            diagnostics.extend(dbt_rev['diagnostics'])
        return False, '; '.join(diagnostics)

    if state == 'needs_context':
        reason = version.get('support_reason', 'Confirm a business date column before comparison')
        return False, f'{label} requires context resolution: {reason}'

    if state == 'unsupported':
        reason = version.get('support_reason', 'SQL is not supported for quantitative analysis')
        return False, f'{label}: {reason}'

    return False, f'{label} has unsupported state: {state}'


# ---------------------------------------------------------------------------
# Core headless comparison executor
# ---------------------------------------------------------------------------

def execute_headless_comparison(
    store: Store,
    inputs: ResolvedInputs,
) -> dict:
    """Execute a deterministic headless comparison and produce a CI report.

    This function reuses the existing MetricGuard comparison engine
    (``packages.metricguard_core.engine.compare``) and V3 materiality
    evaluation.  It does NOT implement a separate calculation engine.

    Returns
    -------
    dict
        A ``metricguard_ci_report_v1`` report dict with ``outcome``,
        ``exit_code``, and full provenance.
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # 1. Gate on support state (exit 2 boundary)
    for label, version in [('Baseline', inputs.baseline_version),
                            ('Target', inputs.target_version)]:
        supported, diagnostic = _check_support_state(version, label)
        if not supported:
            return _build_report(
                inputs=inputs,
                outcome=CIOutcome.REFUSED,
                started_at=started_at,
                diagnostic=diagnostic,
            )

    # 2. Gate on IR availability
    for label, version in [('Baseline', inputs.baseline_version),
                            ('Target', inputs.target_version)]:
        if not version.get('ir') or not version['ir'].get('time_column'):
            return _build_report(
                inputs=inputs,
                outcome=CIOutcome.REFUSED,
                started_at=started_at,
                diagnostic=f'{label} lacks a resolved time column; confirm a business date column.',
            )

    # 3. Gate on snapshot artifact existence
    artifact_path = inputs.snapshot.get('artifact_path')
    if not artifact_path or not Path(artifact_path).is_file():
        return _build_report(
            inputs=inputs,
            outcome=CIOutcome.FAILED,
            started_at=started_at,
            diagnostic='Snapshot artifact file is missing or corrupted.',
        )

    # 4. Verify snapshot integrity (database hash)
    expected_hash = inputs.snapshot.get('database_hash')
    if expected_hash:
        actual_hash = file_hash(artifact_path)
        if actual_hash != expected_hash:
            return _build_report(
                inputs=inputs,
                outcome=CIOutcome.FAILED,
                started_at=started_at,
                diagnostic=f'Snapshot integrity failure: expected hash {expected_hash}, got {actual_hash}.',
            )

    # 5. Execute the comparison using the existing engine
    from packages.metricguard_core.engine import compare as core_compare

    try:
        result = core_compare(
            database=artifact_path,
            baseline=inputs.baseline_version['ir'],
            target=inputs.target_version['ir'],
            context=inputs.context,
        )
    except CoreError as err:
        if err.code in ('not_comparable', 'needs_context', 'period_conflict',
                        'invalid_period', 'snapshot_integrity'):
            return _build_report(
                inputs=inputs,
                outcome=CIOutcome.REFUSED,
                started_at=started_at,
                diagnostic=f'{err.code}: {err.message}',
            )
        # Execution failures → exit 3
        return _build_report(
            inputs=inputs,
            outcome=CIOutcome.FAILED,
            started_at=started_at,
            diagnostic=f'{err.code}: {err.message}',
        )
    except Exception as err:
        return _build_report(
            inputs=inputs,
            outcome=CIOutcome.FAILED,
            started_at=started_at,
            diagnostic=f'Internal execution error: {redact_api_key(str(err))}',
        )

    # 6. Evaluate materiality
    policy = inputs.materiality_policy or {}
    materiality = evaluate_materiality(
        delta_str=result.get('delta_target_minus_baseline'),
        baseline_val_str=result.get('baseline_value'),
        target_val_str=result.get('target_value'),
        absolute_threshold=policy.get('absolute_threshold'),
        relative_threshold=policy.get('relative_threshold'),
        is_intentional_variant=(inputs.relationship == 'intentional_variant'),
        suppress_intentional_variants=policy.get('suppress_intentional_variants', True),
    )

    return _build_report(
        inputs=inputs,
        outcome=CIOutcome.SUPPORTED,
        started_at=started_at,
        comparison_result=result,
        materiality=materiality,
    )


# ---------------------------------------------------------------------------
# Report builder – metricguard_ci_report_v1
# ---------------------------------------------------------------------------

def _safe_decimal(value: str | None) -> str | None:
    """Pass through exact decimal strings; never introduce float ambiguity."""
    if value is None:
        return None
    # Ensure the string is a valid decimal representation
    try:
        Decimal(value)
    except Exception:
        return value
    return value


def _build_report(
    *,
    inputs: ResolvedInputs,
    outcome: CIOutcome,
    started_at: str,
    diagnostic: str | None = None,
    comparison_result: dict | None = None,
    materiality: dict | None = None,
) -> dict:
    """Construct a versioned ``metricguard_ci_report_v1`` report."""
    completed_at = datetime.now(timezone.utc).isoformat()

    is_material = materiality['is_material'] if materiality else False
    exit_code = determine_exit_code(outcome, inputs.mode, is_material)

    run_id = str(uuid4())

    # Baseline/target dbt provenance
    def _dbt_anchor(version: dict) -> dict | None:
        rev = version.get('_dbt_resource_revision')
        if not rev:
            return None
        imp = version.get('_dbt_import_revision') or {}
        return {
            'unique_id': rev.get('unique_id'),
            'resource_type': rev.get('resource_type'),
            'support_state': rev.get('support_state'),
            'import_revision_id': rev.get('import_revision_id'),
            'diagnostics': rev.get('diagnostics'),
            'observed_lineage': rev.get('observed_lineage'),
            'source_revision': imp.get('source_revision'),
            'dbt_version': imp.get('dbt_version'),
            'generated_at': imp.get('generated_at'),
        }


    report: dict[str, Any] = {
        'schema': REPORT_SCHEMA,
        'version': REPORT_VERSION,
        'run_id': run_id,
        'request_hash': inputs.request_hash,
        'project_id': inputs.project_id,

        # Execution control
        'execution_mode': inputs.mode,
        'exit_code': exit_code,
        'outcome': outcome.value,

        # Immutable input references
        'baseline': {
            'definition_version_id': inputs.baseline_version.get('id'),
            'definition_id': inputs.baseline_version.get('definition_id'),
            'ir_hash': inputs.baseline_version.get('ir_hash'),
            'raw_hash': inputs.baseline_version.get('raw_hash'),
            'contract_hash': inputs.baseline_version.get('contract_hash'),
            'mapping_hash': inputs.baseline_version.get('mapping_hash'),
            'support_state': inputs.baseline_version.get('support_state'),
            'dbt': _dbt_anchor(inputs.baseline_version),
        },
        'target': {
            'definition_version_id': inputs.target_version.get('id'),
            'definition_id': inputs.target_version.get('definition_id'),
            'ir_hash': inputs.target_version.get('ir_hash'),
            'raw_hash': inputs.target_version.get('raw_hash'),
            'contract_hash': inputs.target_version.get('contract_hash'),
            'mapping_hash': inputs.target_version.get('mapping_hash'),
            'support_state': inputs.target_version.get('support_state'),
            'dbt': _dbt_anchor(inputs.target_version),
        },
        'snapshot': {
            'snapshot_id': inputs.snapshot.get('id'),
            'manifest_hash': inputs.snapshot.get('manifest_hash'),
            'database_hash': inputs.snapshot.get('database_hash') or inputs.snapshot.get('manifest', {}).get('database_hash'),
            'source_hash': inputs.snapshot.get('source_hash'),
            'source_type': inputs.snapshot.get('source_type') or inputs.snapshot.get('manifest', {}).get('source_type'),
        },
        'context': inputs.context,

        # Materiality
        'materiality_policy': inputs.materiality_policy,
        'materiality': materiality,

        # Operational timestamps (NOT part of comparison identity)
        'started_at': started_at,
        'completed_at': completed_at,
        'cli_version': CLI_VERSION,
    }

    if outcome == CIOutcome.SUPPORTED and comparison_result:
        report['result'] = {
            'baseline_value': _safe_decimal(comparison_result.get('baseline_value')),
            'target_value': _safe_decimal(comparison_result.get('target_value')),
            'delta_target_minus_baseline': _safe_decimal(comparison_result.get('delta_target_minus_baseline')),
            'absolute_gap': _safe_decimal(comparison_result.get('absolute_gap')),
            'residual': _safe_decimal(comparison_result.get('residual')),
            'relative_change_percent_display': _safe_decimal(comparison_result.get('relative_change_percent_display')),
            'value_status': comparison_result.get('value_status'),
            'definition_status': comparison_result.get('definition_status'),
            'explanation_status': comparison_result.get('explanation_status'),
            'method': comparison_result.get('method'),
            'currency': comparison_result.get('currency'),
            'numeric_tolerance': _safe_decimal(comparison_result.get('numeric_tolerance')),
        }
        # Evidence coverage summary (without full row data)
        evidence = comparison_result.get('evidence', [])
        report['evidence_summary'] = {
            'total_rows': len(evidence),
            'included_in_both': sum(1 for r in evidence if r.get('membership') == 'included_in_both'),
            'baseline_only': sum(1 for r in evidence if r.get('membership') == 'baseline_only'),
            'target_only': sum(1 for r in evidence if r.get('membership') == 'target_only'),
            'included_in_neither': sum(1 for r in evidence if r.get('membership') == 'included_in_neither'),
        }
        # Top bridge contributions (deterministic ordering)
        steps = comparison_result.get('steps', [])
        report['bridge_summary'] = [
            {
                'position': s['position'],
                'rule': s['rule'],
                'contribution': _safe_decimal(s['contribution']),
            }
            for s in steps
        ]
        # Provenance from engine
        provenance = comparison_result.get('provenance', {})
        report['provenance'] = {
            'baseline_ir_hash': provenance.get('baseline_ir_hash'),
            'target_ir_hash': provenance.get('target_ir_hash'),
            'snapshot_manifest_hash': provenance.get('snapshot_manifest_hash'),
            'database_hash': provenance.get('database_hash'),
            'source_hash': provenance.get('source_hash'),
            'engine_version': provenance.get('engine_version'),
            'duckdb_version': provenance.get('duckdb_version'),
            'query_count': provenance.get('query_count'),
            'elapsed_ms': provenance.get('elapsed_ms'),
        }
    elif outcome == CIOutcome.REFUSED:
        report['refusal'] = {
            'diagnostic': diagnostic,
        }
    elif outcome == CIOutcome.FAILED:
        report['failure'] = {
            'diagnostic': redact_api_key(diagnostic) if diagnostic else None,
        }

    return report


# ---------------------------------------------------------------------------
# Store-integrated authentication for headless callers
# ---------------------------------------------------------------------------

def authenticate_ci_caller(
    store: Store,
    project_id: str,
    api_key: str | None = None,
) -> dict:
    """Authenticate a CI caller and verify project access.

    Uses the existing V3 authentication model.  API keys are NEVER included
    in return values, reports, logs, or exception messages.

    Parameters
    ----------
    store : Store
        The application store.
    project_id : str
        Target project ID.
    api_key : str | None
        API key from METRICGUARD_API_KEY environment variable.

    Returns
    -------
    dict
        Auth context with user and role information.

    Raises
    ------
    DomainError
        UNAUTHORIZED (401) or FORBIDDEN (403).
    """
    if not api_key:
        # Check if unverified local access is permitted
        allow_unverified = os.environ.get('METRICGUARD_ALLOW_UNVERIFIED_LOCAL', 'true').lower()
        if allow_unverified in ('false', '0', 'no', 'off'):
            raise DomainError('UNAUTHORIZED', 'METRICGUARD_API_KEY is required for CI execution.', 401)
        # Unverified local: only allowed on the default project
        if project_id != store.project_id:
            raise DomainError('FORBIDDEN', f"Authentication required for project '{project_id}'.", 403)
        return {
            'user': {'id': 'local-analyst', 'name': 'Local analyst'},
            'is_unverified': True,
            'role': 'owner',
            'project_id': project_id,
        }

    user = store.get_user_by_api_key(api_key)
    if not user:
        raise DomainError('UNAUTHORIZED', 'Invalid or inactive API key.', 401)

    member = store.get_project_member(project_id, user['id'])
    if not member or member.get('revoked_at') is not None:
        raise DomainError('FORBIDDEN', f"Access denied to project '{project_id}'.", 403)

    if member['role'] not in ('owner', 'analyst'):
        raise DomainError(
            'FORBIDDEN',
            f"Role '{member['role']}' is not permitted to execute CI comparisons in project '{project_id}'.",
            403,
        )

    return {
        'user': user,
        'is_unverified': False,
        'role': member['role'],
        'project_id': project_id,
    }


# ---------------------------------------------------------------------------
# Terminal & Markdown report generators
# ---------------------------------------------------------------------------

def generate_terminal_summary(report: dict) -> str:
    """Produce a deterministic ASCII terminal summary from a CI report dict.

    Never introduces analytical narrative or unverified explanations.
    All credentials are automatically redacted.
    """
    lines = []
    bar = '=' * 80

    lines.append(bar)
    lines.append('                         METRICGUARD CI COMPARISON')
    lines.append(bar)

    outcome = str(report.get('outcome', 'unknown')).upper()
    exit_code = report.get('exit_code', '?')
    mode = report.get('execution_mode', 'advisory')
    project_id = report.get('project_id', 'unknown')

    # Determine CI State badge
    if outcome == 'SUPPORTED':
        is_material = report.get('materiality', {}).get('is_material', False) if report.get('materiality') else False
        if exit_code == 0:
            ci_state = 'PASS (Supported Agreement)' if not is_material else 'PASS (Advisory Drift - Policy Exceeded)'
        else:
            ci_state = 'FAIL (Material Drift Exceeded in Enforcing Mode)'
    elif outcome == 'REFUSED':
        ci_state = 'REFUSED (Quantitative Analysis Refused - Exit Code 2)'
    elif outcome == 'FAILED':
        ci_state = 'FAILED (Operational Failure - Exit Code 3)'
    else:
        ci_state = f'{outcome} (Exit Code: {exit_code})'

    lines.append(f'Status:          {outcome} (Exit code: {exit_code})')
    lines.append(f'CI State:        {ci_state}')
    lines.append(f'Execution Mode:  {mode}')
    lines.append(f'Project ID:      {project_id}')

    req_hash = report.get('request_hash')
    if req_hash:
        lines.append(f'Request Hash:    {req_hash}')

    run_id = report.get('run_id')
    if run_id:
        lines.append(f'Run ID:          {run_id}')

    # Provenance / comparison identifiers
    baseline = report.get('baseline') or {}
    target = report.get('target') or {}
    snapshot = report.get('snapshot') or {}

    if baseline or target or snapshot:
        lines.append('')
        lines.append('--- Comparison Identity ' + '-' * 56)
        if baseline:
            b_ver = baseline.get('definition_version_id', 'unknown')
            b_state = baseline.get('support_state', 'unknown')
            b_dbt = baseline.get('dbt')
            dbt_txt = f" (dbt: {b_dbt['unique_id']})" if b_dbt and b_dbt.get('unique_id') else ""
            lines.append(f'Baseline:        {b_ver} [{b_state}]{dbt_txt}')
        if target:
            t_ver = target.get('definition_version_id', 'unknown')
            t_state = target.get('support_state', 'unknown')
            t_dbt = target.get('dbt')
            dbt_txt = f" (dbt: {t_dbt['unique_id']})" if t_dbt and t_dbt.get('unique_id') else ""
            lines.append(f'Target:          {t_ver} [{t_state}]{dbt_txt}')
        if snapshot:
            s_id = snapshot.get('snapshot_id', 'unknown')
            s_hash = snapshot.get('manifest_hash') or snapshot.get('database_hash')
            hash_txt = f' (hash: {s_hash[:16]}...)' if s_hash else ''
            lines.append(f'Snapshot:        {s_id}{hash_txt}')

    # If outcome == SUPPORTED, show metric values, materiality, evidence, bridge
    if outcome == 'SUPPORTED' and 'result' in report:
        res = report['result']
        curr = res.get('currency') or ''
        curr_suffix = f' {curr}' if curr else ''

        lines.append('')
        lines.append('--- Metric Values & Drift ' + '-' * 54)
        b_val = res.get('baseline_value', 'N/A')
        t_val = res.get('target_value', 'N/A')
        delta = res.get('delta_target_minus_baseline', 'N/A')
        pct = res.get('relative_change_percent_display')
        pct_txt = f' ({pct}%)' if pct is not None else ''

        lines.append(f'Baseline Value:           {b_val}{curr_suffix}')
        lines.append(f'Target Value:             {t_val}{curr_suffix}')
        lines.append(f'Delta (Target - Base):    {delta}{curr_suffix}{pct_txt}')
        lines.append(f'Absolute Gap:             {res.get("absolute_gap", "N/A")}')
        lines.append(f'Value Status:             {res.get("value_status", "N/A")}')

        # Materiality
        mat = report.get('materiality') or {}
        is_mat = mat.get('is_material', False)
        mat_verdict = 'MATERIAL DRIFT' if is_mat else 'WITHIN TOLERANCE'
        lines.append(f'Materiality Verdict:      {mat_verdict} (is_material={is_mat})')
        if mat.get('reason'):
            lines.append(f'Materiality Reason:       {mat.get("reason")}')

        # Evidence summary
        ev = report.get('evidence_summary')
        if ev:
            lines.append('')
            lines.append('--- Row Evidence Summary ' + '-' * 55)
            lines.append(f'Total Evaluated Rows:     {ev.get("total_rows", 0):,}')
            lines.append(f'Included in Both:         {ev.get("included_in_both", 0):,}')
            lines.append(f'Baseline Only:            {ev.get("baseline_only", 0):,}')
            lines.append(f'Target Only:              {ev.get("target_only", 0):,}')
            lines.append(f'Included in Neither:      {ev.get("included_in_neither", 0):,}')

        # Bridge summary
        bridge = report.get('bridge_summary')
        if bridge:
            lines.append('')
            lines.append('--- Bridge Decomposition (Top Steps) ' + '-' * 43)
            for step in bridge:
                pos = step.get('position', '?')
                rule = step.get('rule', 'unknown')
                contrib = step.get('contribution', '0')
                lines.append(f'  {pos}. {rule}: {contrib}{curr_suffix}')

    elif outcome == 'REFUSED':
        ref = report.get('refusal') or {}
        diag = ref.get('diagnostic') or report.get('diagnostic') or 'Analysis refused by MetricGuard'
        lines.append('')
        lines.append('--- Refusal Diagnostic ' + '-' * 57)
        lines.append(f'Reason:          {diag}')
        lines.append('Note:            MetricGuard refused quantitative analysis for this selector.')
        lines.append('                 (Exit code 2: unsupported construct, metadata-only, or missing context)')

    elif outcome == 'FAILED':
        fail = report.get('failure') or {}
        diag = fail.get('diagnostic') or report.get('diagnostic') or 'Operational failure'
        lines.append('')
        lines.append('--- Operational Failure ' + '-' * 56)
        lines.append(f'Diagnostic:      {diag}')
        lines.append('Note:            Comparison could not execute due to operational failure.')
        lines.append('                 (Exit code 3: snapshot missing/corrupted, bad inputs, or auth error)')

    lines.append('')
    lines.append(f'Final CI Exit Code: {exit_code}')
    lines.append(bar)

    summary_text = '\n'.join(lines)
    return redact_api_key(summary_text)


def generate_markdown_report(report: dict) -> str:
    """Produce a human-readable Markdown report appropriate for CI artifacts and PR comments.

    Deterministic presentation only; never includes AI narrative.
    All credentials are automatically redacted.
    """
    lines = []
    lines.append('# MetricGuard CI Comparison Report')
    lines.append('')

    outcome = str(report.get('outcome', 'unknown')).upper()
    exit_code = report.get('exit_code', '?')
    mode = report.get('execution_mode', 'advisory')
    project_id = report.get('project_id', 'unknown')

    # CI State badge
    if outcome == 'SUPPORTED':
        is_material = report.get('materiality', {}).get('is_material', False) if report.get('materiality') else False
        if exit_code == 0:
            badge = 'PASS (Supported Agreement)' if not is_material else 'PASS (Advisory Drift)'
        else:
            badge = 'FAIL (Material Drift Exceeded in Enforcing Mode)'
    elif outcome == 'REFUSED':
        badge = 'REFUSED (Quantitative Analysis Refused - Exit Code 2)'
    elif outcome == 'FAILED':
        badge = 'FAILED (Operational Failure - Exit Code 3)'
    else:
        badge = f'{outcome} (Exit Code {exit_code})'

    lines.append('| Property | Value |')
    lines.append('| :--- | :--- |')
    lines.append(f'| **Status** | {badge} |')
    lines.append(f'| **Exit Code** | `{exit_code}` |')
    lines.append(f'| **Execution Mode** | `{mode}` |')
    lines.append(f'| **Project ID** | `{project_id}` |')
    if report.get('request_hash'):
        lines.append(f'| **Request Hash** | `{report["request_hash"]}` |')
    if report.get('run_id'):
        lines.append(f'| **Run ID** | `{report["run_id"]}` |')
    lines.append(f'| **CLI Version** | `{report.get("cli_version", CLI_VERSION)}` |')
    lines.append('')
    lines.append('---')
    lines.append('')

    # Identity
    baseline = report.get('baseline') or {}
    target = report.get('target') or {}
    snapshot = report.get('snapshot') or {}

    lines.append('## 1. Comparison Identity')
    lines.append('')
    lines.append('| Role | Definition Version ID | Support State | dbt Resource |')
    lines.append('| :--- | :--- | :--- | :--- |')
    b_dbt = baseline.get('dbt') or {}
    b_dbt_txt = f"`{b_dbt.get('unique_id')}`" if b_dbt.get('unique_id') else '-'
    lines.append(f'| **Baseline** | `{baseline.get("definition_version_id", "N/A")}` | `{baseline.get("support_state", "unknown")}` | {b_dbt_txt} |')
    t_dbt = target.get('dbt') or {}
    t_dbt_txt = f"`{t_dbt.get('unique_id')}`" if t_dbt.get('unique_id') else '-'
    lines.append(f'| **Target** | `{target.get("definition_version_id", "N/A")}` | `{target.get("support_state", "unknown")}` | {t_dbt_txt} |')
    lines.append('')
    lines.append(f'- **Snapshot ID:** `{snapshot.get("snapshot_id", "N/A")}`')
    if snapshot.get('manifest_hash'):
        lines.append(f'- **Snapshot Manifest Hash:** `{snapshot["manifest_hash"]}`')
    if snapshot.get('database_hash'):
        lines.append(f'- **Database Hash:** `{snapshot["database_hash"]}`')
    if snapshot.get('source_type'):
        lines.append(f'- **Source Type:** `{snapshot["source_type"]}`')

    def _format_lineage(lineage: Any) -> str:
        if isinstance(lineage, list):
            return ', '.join(str(x) for x in lineage)
        if isinstance(lineage, dict):
            up = lineage.get('upstream_nodes') or []
            down = lineage.get('downstream_nodes') or []
            parts = []
            if up:
                parts.append(f"upstream: {', '.join(str(x) for x in up)}")
            if down:
                parts.append(f"downstream: {', '.join(str(x) for x in down)}")
            return '; '.join(parts) if parts else str(lineage)
        return str(lineage)

    # dbt revision metadata if available
    dbt_lines = []
    if b_dbt.get('import_revision_id'):
        dbt_lines.append(f"- **Baseline dbt Import Revision:** `{b_dbt['import_revision_id']}`")
    if t_dbt.get('import_revision_id'):
        dbt_lines.append(f"- **Target dbt Import Revision:** `{t_dbt['import_revision_id']}`")
    if b_dbt.get('source_revision'):
        sr = b_dbt['source_revision']
        sr_txt = f"{sr.get('git_sha', '')} ({sr.get('branch', '')})".strip() if isinstance(sr, dict) else str(sr)
        dbt_lines.append(f"- **Baseline Source Revision:** `{sr_txt}`")
    if t_dbt.get('source_revision'):
        sr = t_dbt['source_revision']
        sr_txt = f"{sr.get('git_sha', '')} ({sr.get('branch', '')})".strip() if isinstance(sr, dict) else str(sr)
        dbt_lines.append(f"- **Target Source Revision:** `{sr_txt}`")
    if b_dbt.get('observed_lineage'):
        dbt_lines.append(f"- **Baseline Observed Lineage:** `{_format_lineage(b_dbt['observed_lineage'])}`")
    if t_dbt.get('observed_lineage'):
        dbt_lines.append(f"- **Target Observed Lineage:** `{_format_lineage(t_dbt['observed_lineage'])}`")

    if dbt_lines:
        lines.append('')
        lines.append('### dbt Provenance Metadata')
        lines.extend(dbt_lines)

    lines.append('')
    lines.append('---')
    lines.append('')

    if outcome == 'SUPPORTED' and 'result' in report:
        res = report['result']
        mat = report.get('materiality') or {}
        curr = res.get('currency') or ''
        curr_suffix = f' {curr}' if curr else ''

        lines.append('## 2. Metric Results & Materiality')
        lines.append('')
        lines.append('| Metric Property | Value |')
        lines.append('| :--- | :--- |')
        lines.append(f'| **Baseline Value** | `{res.get("baseline_value", "N/A")}{curr_suffix}` |')
        lines.append(f'| **Target Value** | `{res.get("target_value", "N/A")}{curr_suffix}` |')
        pct = res.get('relative_change_percent_display')
        pct_txt = f' ({pct}%)' if pct is not None else ''
        lines.append(f'| **Delta (Target - Baseline)** | `{res.get("delta_target_minus_baseline", "N/A")}{curr_suffix}`{pct_txt} |')
        lines.append(f'| **Absolute Gap** | `{res.get("absolute_gap", "N/A")}` |')
        lines.append(f'| **Value Status** | `{res.get("value_status", "N/A")}` |')
        is_mat = mat.get('is_material', False)
        verdict_str = '**Material Drift**' if is_mat else '**Within Tolerance**'
        lines.append(f'| **Materiality Verdict** | {verdict_str} (`is_material={is_mat}`) |')
        if mat.get('threshold_type'):
            lines.append(f'| **Threshold Evaluated** | `{mat.get("threshold_type")}` |')
        if mat.get('reason'):
            lines.append(f'| **Materiality Reason** | {mat.get("reason")} |')

        lines.append('')
        lines.append('---')
        lines.append('')

        ev = report.get('evidence_summary')
        if ev:
            lines.append('## 3. Evidence & Row Attribution')
            lines.append('')
            lines.append('| Classification | Row Count |')
            lines.append('| :--- | :--- |')
            lines.append(f'| **Total Evaluated Rows** | `{ev.get("total_rows", 0):,}` |')
            lines.append(f'| **Included in Both** | `{ev.get("included_in_both", 0):,}` |')
            lines.append(f'| **Baseline Only** | `{ev.get("baseline_only", 0):,}` |')
            lines.append(f'| **Target Only** | `{ev.get("target_only", 0):,}` |')
            lines.append(f'| **Included in Neither** | `{ev.get("included_in_neither", 0):,}` |')
            lines.append('')
            lines.append('---')
            lines.append('')

        bridge = report.get('bridge_summary')
        if bridge:
            lines.append('## 4. Bridge Attribution Summary')
            lines.append('')
            lines.append('| Step | Rule / Transform | Contribution |')
            lines.append('| :--- | :--- | :--- |')
            for step in bridge:
                pos = step.get('position', '?')
                rule = step.get('rule', 'unknown')
                contrib = step.get('contribution', '0')
                lines.append(f'| {pos} | `{rule}` | `{contrib}{curr_suffix}` |')
            lines.append('')
            lines.append('---')
            lines.append('')

        prov = report.get('provenance')
        if prov:
            lines.append('## 5. Execution Provenance')
            lines.append('')
            lines.append(f'- **Engine Version:** `{prov.get("engine_version", "N/A")}`')
            lines.append(f'- **DuckDB Version:** `{prov.get("duckdb_version", "N/A")}`')
            lines.append(f'- **Query Count:** `{prov.get("query_count", "N/A")}`')
            lines.append(f'- **Elapsed Time:** `{prov.get("elapsed_ms", "N/A")} ms`')
            lines.append(f'- **Started At:** `{report.get("started_at", "N/A")}`')
            lines.append(f'- **Completed At:** `{report.get("completed_at", "N/A")}`')
            lines.append('')

    elif outcome == 'REFUSED':
        ref = report.get('refusal') or {}
        diag = ref.get('diagnostic') or report.get('diagnostic') or 'Quantitative analysis refused.'
        lines.append('## 2. Refusal Diagnostic')
        lines.append('')
        lines.append('> **Refusal Diagnostic (Exit Code 2):**  ')
        lines.append(f'> {diag}')
        lines.append('')
        lines.append('MetricGuard cannot perform quantitative reconciliation between these definitions. '
                     'This occurs when one or more definitions use unsupported SQL constructs (such as '
                     'multi-table JOINs, CTEs, or unrendered Jinja macros), a metadata-only dbt resource, '
                     'or missing context.')
        lines.append('')

    elif outcome == 'FAILED':
        fail = report.get('failure') or {}
        diag = fail.get('diagnostic') or report.get('diagnostic') or 'Operational failure.'
        lines.append('## 2. Operational Failure Diagnostic')
        lines.append('')
        lines.append('> **Operational Error (Exit Code 3):**  ')
        lines.append(f'> {diag}')
        lines.append('')
        lines.append('An operational failure prevented MetricGuard from completing the comparison. '
                     'Verify snapshot file existence and integrity, store database connectivity, and caller authorization.')
        lines.append('')

    md_text = '\n'.join(lines)
    return redact_api_key(md_text)


# ---------------------------------------------------------------------------
# CLI Argument Parser with strict exit-code mapping
# ---------------------------------------------------------------------------

class ArgumentParsingError(Exception):
    """Raised when command-line arguments cannot be parsed or are invalid."""
    pass


class MetricGuardArgumentParser(argparse.ArgumentParser):
    """Custom parser that raises ArgumentParsingError instead of sys.exit(2).

    This ensures argument syntax and validation failures map to exit code 3 (operational failure)
    rather than exit code 2 (which is strictly reserved for domain refusals).
    """

    def error(self, message: str) -> None:
        safe_msg = redact_api_key(message)
        raise ArgumentParsingError(safe_msg)


# ---------------------------------------------------------------------------
# Report emission
# ---------------------------------------------------------------------------

def _emit_report(
    report: dict,
    output_path: str | None = None,
    markdown_path: str | None = None,
    format_mode: str = 'summary',
) -> None:
    """Write the report to JSON/Markdown file(s) and/or stdout, never including credentials."""
    if output_path:
        out_p = Path(output_path)
        if out_p.parent and not out_p.parent.exists():
            out_p.parent.mkdir(parents=True, exist_ok=True)
        json_text = json.dumps(report, indent=2, default=str)
        json_text = redact_api_key(json_text)
        out_p.write_text(json_text, encoding='utf-8')

    if markdown_path:
        md_p = Path(markdown_path)
        if md_p.parent and not md_p.parent.exists():
            md_p.parent.mkdir(parents=True, exist_ok=True)
        md_text = generate_markdown_report(report)
        md_text = redact_api_key(md_text)
        md_p.write_text(md_text, encoding='utf-8')

    if format_mode == 'quiet':
        return
    elif format_mode == 'json':
        json_text = json.dumps(report, indent=2, default=str)
        print(redact_api_key(json_text))
    else:  # 'summary'
        print(generate_terminal_summary(report))


def generate_diff_terminal_summary(report: dict) -> str:
    bar = '=' * 70
    sub_bar = '-' * 70
    lines = [
        bar,
        '  MetricGuard Revision Metric Diff',
        bar,
        f"Overall Outcome:    {report.get('overall_outcome', 'unknown')}",
        f"Exit Code:          {report.get('exit_code', '?')}",
        f"Execution Mode:     {report.get('mode', 'advisory')}",
        f"Project ID:         {report.get('project_id', 'unknown')}",
        f"Baseline Revision:  {report.get('baseline_revision_id', 'unknown')}",
        f"Target Revision:    {report.get('target_revision_id', 'unknown')}",
        f"Snapshot ID:        {report.get('snapshot_id', 'unknown')}",
        sub_bar,
    ]
    counts = report.get('counts', {})
    lines.extend([
        f"Total Changed Res:  {counts.get('total_changed_resources', 0)}",
        f"Declared Affected:  {counts.get('total_declared_metrics_affected', 0)}",
        f"Analyzed Metrics:   {counts.get('successfully_analyzed_metrics', 0)}",
        f"Refused Metrics:    {counts.get('refused_metrics', 0)}",
        f"Material Metrics:   {counts.get('material_metrics', 0)}",
        f"Undeclared Res:     {counts.get('undeclared_changed_resources', 0)}",
        bar,
    ])
    return redact_api_key('\n'.join(lines))


def _emit_diff_report(
    report: dict,
    output_path: str | None = None,
    markdown_path: str | None = None,
    format_mode: str = 'summary',
) -> None:
    from services.diff_report import format_metric_diff_markdown

    if output_path:
        out_p = Path(output_path)
        if out_p.parent and not out_p.parent.exists():
            out_p.parent.mkdir(parents=True, exist_ok=True)
        json_text = json.dumps(report, indent=2, default=str)
        json_text = redact_api_key(json_text)
        out_p.write_text(json_text, encoding='utf-8')

    if markdown_path:
        md_p = Path(markdown_path)
        if md_p.parent and not md_p.parent.exists():
            md_p.parent.mkdir(parents=True, exist_ok=True)
        md_text = format_metric_diff_markdown(report)
        md_text = redact_api_key(md_text)
        md_p.write_text(md_text, encoding='utf-8')

    if format_mode == 'quiet':
        return
    elif format_mode == 'json':
        json_text = json.dumps(report, indent=2, default=str)
        print(redact_api_key(json_text))
    else:  # 'summary'
        print(generate_diff_terminal_summary(report))


def _run_diff_cli(args, parser, argv=None) -> int:
    output_path = args.output
    markdown_path = args.markdown_output

    format_mode = 'summary'
    if args.quiet:
        format_mode = 'quiet'
    elif args.json or args.format == 'json':
        format_mode = 'json'
    elif args.format:
        format_mode = args.format

    api_key = safe_env_api_key()
    project_id = args.project_id or os.environ.get('METRICGUARD_PROJECT_ID')

    if not project_id:
        print('Error: --project-id or METRICGUARD_PROJECT_ID is required', file=sys.stderr)
        return 3

    context = None
    context_raw = args.context or args.context_json
    if context_raw:
        try:
            if os.path.isfile(context_raw):
                with open(context_raw, 'r', encoding='utf-8') as cp:
                    context = json.load(cp)
            else:
                context = json.loads(context_raw)
            if not isinstance(context, dict):
                raise ValueError('Context must be a JSON object')
        except Exception as err:
            safe_err = redact_api_key(str(err))
            print(f'Error: invalid comparison context: {safe_err}', file=sys.stderr)
            return 3

    mat_policy = None
    if args.materiality_policy:
        try:
            if os.path.isfile(args.materiality_policy):
                with open(args.materiality_policy, 'r', encoding='utf-8') as mp:
                    mat_policy = json.load(mp)
            else:
                mat_policy = json.loads(args.materiality_policy)
            if not isinstance(mat_policy, dict):
                raise ValueError('Materiality policy must be a JSON object')
        except Exception as err:
            safe_err = redact_api_key(str(err))
            print(f'Error: invalid --materiality-policy: {safe_err}', file=sys.stderr)
            return 3

    if args.absolute_threshold is not None or args.relative_threshold is not None:
        if mat_policy is None:
            mat_policy = {}
        if args.absolute_threshold is not None:
            mat_policy['absolute_threshold'] = str(args.absolute_threshold)
        if args.relative_threshold is not None:
            mat_policy['relative_threshold'] = args.relative_threshold

    db_url = args.database_url or os.environ.get('METRICGUARD_DATABASE_URL')
    if not db_url:
        data_dir = Path(args.data_dir or os.environ.get('METRICGUARD_DATA_DIR', 'data'))
        db_url = f'sqlite:///{data_dir / "metricguard.db"}'

    try:
        store = Store(db_url)
        auth = authenticate_ci_caller(store, project_id, api_key)
        snapshot = resolve_snapshot(store, args.snapshot, project_id)
        report = store.execute_metric_diff(
            baseline_revision_id=args.baseline,
            target_revision_id=args.target,
            snapshot_id=snapshot['id'],
            mode=args.mode,
            context=context,
            materiality_policy=mat_policy,
            project_id=project_id,
        )
    except DomainError as err:
        exit_code = 2 if err.code in _REFUSAL_CODES else 3
        safe_msg = redact_api_key(err.message)
        error_report = {
            'schema_version': 'metricguard_metric_diff_v1',
            'id': f"diff-{uuid4().hex}",
            'project_id': project_id,
            'baseline_revision_id': getattr(args, 'baseline', None),
            'target_revision_id': getattr(args, 'target', None),
            'snapshot_id': getattr(args, 'snapshot', None),
            'mode': getattr(args, 'mode', 'advisory'),
            'overall_outcome': 'completed_with_refusals' if exit_code == 2 else 'operational_failure',
            'exit_code': exit_code,
            'diagnostic': safe_msg,
            'counts': {
                'total_changed_resources': 0,
                'unchanged_resources': 0,
                'total_declared_metrics_affected': 0,
                'successfully_analyzed_metrics': 0,
                'refused_metrics': 1 if exit_code == 2 else 0,
                'undeclared_changed_resources': 0,
                'material_metrics': 0,
            },
            'refusals': [{
                'metric_id': None,
                'metric_display_name': None,
                'resource_id': getattr(args, 'target', 'unknown'),
                'support_state': 'unsupported',
                'exact_diagnostic': safe_msg,
                'diagnostics': [safe_msg],
                'refusal_stage': 'support_gate' if exit_code == 2 else 'operational',
            }] if exit_code == 2 else [],
            'metrics': [],
            'candidate_metrics': [],
            'undeclared_resources': [],
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        try:
            _emit_diff_report(
                error_report,
                output_path=output_path,
                markdown_path=markdown_path,
                format_mode=format_mode,
            )
        except OSError as emit_err:
            safe_emit_err = redact_api_key(str(emit_err))
            print(f"Error: failed to write output file: {safe_emit_err}", file=sys.stderr)
            return 3
        return exit_code
    except Exception as err:
        safe_msg = redact_api_key(str(err))
        error_report = {
            'schema_version': 'metricguard_metric_diff_v1',
            'id': f"diff-{uuid4().hex}",
            'project_id': project_id,
            'baseline_revision_id': getattr(args, 'baseline', None),
            'target_revision_id': getattr(args, 'target', None),
            'snapshot_id': getattr(args, 'snapshot', None),
            'mode': getattr(args, 'mode', 'advisory'),
            'overall_outcome': 'operational_failure',
            'exit_code': 3,
            'diagnostic': safe_msg,
            'failure': {'diagnostic': safe_msg},
            'counts': {
                'total_changed_resources': 0,
                'unchanged_resources': 0,
                'total_declared_metrics_affected': 0,
                'successfully_analyzed_metrics': 0,
                'refused_metrics': 0,
                'undeclared_changed_resources': 0,
                'material_metrics': 0,
            },
            'refusals': [],
            'metrics': [],
            'candidate_metrics': [],
            'undeclared_resources': [],
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        try:
            _emit_diff_report(
                error_report,
                output_path=output_path,
                markdown_path=markdown_path,
                format_mode=format_mode,
            )
        except OSError as emit_err:
            safe_emit_err = redact_api_key(str(emit_err))
            print(f"Error: failed to write output file: {safe_emit_err}", file=sys.stderr)
            return 3
        return 3

    try:
        _emit_diff_report(
            report,
            output_path=output_path,
            markdown_path=markdown_path,
            format_mode=format_mode,
        )
    except OSError as err:
        safe_msg = redact_api_key(str(err))
        print(f"Error: failed to write output file: {safe_msg}", file=sys.stderr)
        return 3

    return report['exit_code']


# ---------------------------------------------------------------------------
# Declarative Repository Configuration Validation (V6.2)
# ---------------------------------------------------------------------------

def _run_validate_config_cli(args: Any, parser: Any, argv: list[str] | None) -> int:
    """Execute declarative repository configuration validation (V6.2)."""
    from services.config import validate_config, ConfigProvenance, ConfigErrorCode

    format_mode = 'summary'
    if getattr(args, 'quiet', False):
        format_mode = 'quiet'
    elif getattr(args, 'json', False) or getattr(args, 'format', None) == 'json':
        format_mode = 'json'
    elif getattr(args, 'format', None):
        format_mode = args.format

    db_url = getattr(args, 'database_url', None) or os.environ.get('METRICGUARD_DATABASE_URL')
    if not db_url and not getattr(args, 'schema_only', False):
        data_dir = Path(getattr(args, 'data_dir', None) or os.environ.get('METRICGUARD_DATA_DIR', 'data'))
        db_url = f'sqlite:///{data_dir / "metricguard.db"}'

    try:
        prov = ConfigProvenance(getattr(args, 'provenance', 'local_user'))
    except Exception:
        prov = ConfigProvenance.LOCAL_USER

    result = validate_config(
        config_source=Path(getattr(args, 'config', 'metricguard.yml')),
        manifest_path=getattr(args, 'manifest', None),
        schema_only=getattr(args, 'schema_only', False),
        database_url=db_url,
        provenance=prov,
    )

    result_dict = result.to_dict()

    output_path = getattr(args, 'output', None)
    if output_path:
        try:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(result_dict, indent=2), encoding='utf-8')
        except OSError as err:
            safe_err = redact_api_key(str(err))
            print(f"Error: failed to write output file: {safe_err}", file=sys.stderr)
            return 3

    if format_mode == 'json':
        print(json.dumps(result_dict, indent=2))
    elif format_mode == 'summary':
        if result.is_valid:
            print("MetricGuard config validated successfully.")
            if result.loaded_config:
                cfg = result.loaded_config.config
                print(f"  Workspace:               {cfg.workspace}")
                print(f"  Config version:          {cfg.version}")
                print(f"  Config hash:             {result.loaded_config.config_hash}")
                print(f"  Metrics verified:        {result.metrics_verified}")
                if getattr(args, 'manifest', None):
                    print(f"  dbt resources verified:  {result.dbt_resources_verified}")
                print(f"  Validation mode:         {result.mode}")
                print(f"  Store connected:         {result.store_connected}")
                if result.loaded_config.effective_app_url:
                    trust_label = "trusted" if result.loaded_config.effective_app_url_trusted else "untrusted"
                    print(f"  App URL:                 {result.loaded_config.effective_app_url} ({trust_label})")
        else:
            print(f"MetricGuard config validation FAILED with {len(result.issues)} issue(s):", file=sys.stderr)
            for idx, issue in enumerate(result.issues, 1):
                loc_part = f" at '{issue.field_path}'" if issue.field_path else ""
                sug_part = f"\n    Suggested: {issue.suggested_correction}" if issue.suggested_correction else ""
                print(f"  {idx}. [{issue.code}]{loc_part}: {issue.message}{sug_part}", file=sys.stderr)

    if result.is_valid:
        return 0

    operational_codes = {
        ConfigErrorCode.CONFIG_FILE_NOT_FOUND.value,
        ConfigErrorCode.STORE_UNAVAILABLE.value,
        ConfigErrorCode.INVALID_MANIFEST.value,
    }
    if any(i.code in operational_codes for i in result.issues):
        return 3

    return 2


# ---------------------------------------------------------------------------
# Turnkey CI / PR Metric Change Gate (V6.3)
# ---------------------------------------------------------------------------

def _run_diff_ci_cli(args: Any, parser: Any, argv: list[str] | None) -> int:
    """Execute turnkey provider-neutral CI/PR metric diff change gate (V6.3)."""
    from services.ci_workflow import execute_diff_ci, redact_ci_secrets
    from services.config import ConfigProvenance

    format_mode = 'summary'
    if getattr(args, 'quiet', False):
        format_mode = 'quiet'
    elif getattr(args, 'json', False) or getattr(args, 'format', None) == 'json':
        format_mode = 'json'
    elif getattr(args, 'format', None):
        format_mode = args.format

    db_url = getattr(args, 'database_url', None) or os.environ.get('METRICGUARD_DATABASE_URL')
    if not db_url:
        data_dir = Path(getattr(args, 'data_dir', None) or os.environ.get('METRICGUARD_DATA_DIR', 'data'))
        db_url = f'sqlite:///{data_dir / "metricguard.db"}'

    try:
        prov = ConfigProvenance(getattr(args, 'provenance', 'local_user'))
    except Exception:
        prov = ConfigProvenance.LOCAL_USER

    mat_policy = None
    if getattr(args, 'materiality_policy', None):
        try:
            if os.path.isfile(args.materiality_policy):
                with open(args.materiality_policy, 'r', encoding='utf-8') as mp:
                    mat_policy = json.load(mp)
            else:
                mat_policy = json.loads(args.materiality_policy)
            if not isinstance(mat_policy, dict):
                raise ValueError('Materiality policy must be a JSON object')
        except Exception as err:
            safe_err = redact_ci_secrets(str(err))
            print(f'Error: invalid --materiality-policy: {safe_err}', file=sys.stderr)
            return 3

    result = execute_diff_ci(
        config_path=Path(getattr(args, 'config', 'metricguard.yml')),
        base_manifest_path=Path(args.base_manifest),
        target_manifest_path=Path(args.target_manifest),
        base_config_path=Path(args.base_config) if getattr(args, 'base_config', None) else None,
        database_url=db_url,
        data_dir=getattr(args, 'data_dir', None),
        provenance=prov,
        materiality_policy=mat_policy,
    )

    # Write output JSON artifact if requested
    out_json_path = getattr(args, 'output_json', None)
    if out_json_path:
        try:
            out_p = Path(out_json_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(result.report, indent=2), encoding='utf-8')
        except OSError as err:
            safe_err = redact_ci_secrets(str(err))
            print(f"Error: failed to write JSON report: {safe_err}", file=sys.stderr)
            return 3

    # Write output Markdown artifact if requested
    out_md_path = getattr(args, 'output_markdown', None)
    if out_md_path:
        try:
            out_m = Path(out_md_path)
            out_m.parent.mkdir(parents=True, exist_ok=True)
            out_m.write_text(result.markdown, encoding='utf-8')
        except OSError as err:
            safe_err = redact_ci_secrets(str(err))
            print(f"Error: failed to write Markdown summary: {safe_err}", file=sys.stderr)
            return 3

    # Terminal output
    if format_mode == 'json':
        print(json.dumps(result.report, indent=2))
    elif format_mode == 'summary':
        # Print concise Zero-Green summary to stdout / stderr
        print(result.markdown)

    return result.exit_code


# ---------------------------------------------------------------------------
# Entry point – Headless CLI comparison
# ---------------------------------------------------------------------------

def run_cli(argv: list[str] | None = None) -> int:
    """Headless CLI entry point for MetricGuard CI comparisons.

    Exit codes:
      0  Supported analysis completed (agreement or advisory drift).
      1  Supported analysis completed in enforcing mode with material drift.
      2  MetricGuard refused quantitative analysis (unsupported construct/state).
      3  Operational failure (missing artifact, invalid arguments, auth failure).
    """
    import argparse

    parser = MetricGuardArgumentParser(
        prog='metricguard-ci',
        description='MetricGuard Headless CI Comparison and Verification Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit Codes:
  0  Supported analysis completed (agreement or advisory drift).
  1  Supported analysis completed in enforcing mode with material drift.
  2  MetricGuard refused quantitative analysis (unsupported construct/state).
  3  Operational failure (missing artifact, invalid arguments, auth failure).

Examples:
  # Basic advisory comparison
  python -m services.cli compare --project my-project \\
      --baseline dbt:model.orders_v1 --target dbt:model.orders_v2 \\
      --snapshot snap-12345 --context-json '{"start":"2026-08-01","end_exclusive":"2026-09-01"}'

  # Enforcing mode with JSON and Markdown reports
  python -m services.cli compare --project my-project \\
      --baseline def-ver-1 --target def-ver-2 --snapshot snap-12345 \\
      --mode enforcing --absolute-threshold 50.0 \\
      --context context.json --output report.json --markdown-output report.md
""",
    )
    sub = parser.add_subparsers(dest='command')

    compare_parser = sub.add_parser(
        'compare',
        help='Run a deterministic headless comparison between two metric definitions',
        description='Execute deterministic comparison between baseline and target definition versions on an immutable snapshot.',
    )
    compare_parser.add_argument(
        '--project-id', '--project',
        dest='project_id',
        required=False,
        default=None,
        help='Target project ID (defaults to METRICGUARD_PROJECT_ID environment variable)',
    )
    compare_parser.add_argument(
        '--baseline',
        dest='baseline',
        required=True,
        help='Baseline definition version ID or dbt resource selector (e.g. dbt:<unique_id>)',
    )
    compare_parser.add_argument(
        '--target',
        dest='target',
        required=True,
        help='Target definition version ID or dbt resource selector (e.g. dbt:<unique_id>)',
    )
    compare_parser.add_argument(
        '--snapshot',
        dest='snapshot',
        required=True,
        help='Snapshot ID or selector (e.g. <uuid> or snapshot:<uuid>)',
    )
    compare_parser.add_argument(
        '--mode',
        dest='mode',
        choices=['advisory', 'enforcing'],
        default='advisory',
        help="Execution mode: 'advisory' (drift exits 0) or 'enforcing' (material drift exits 1) [default: advisory]",
    )
    compare_parser.add_argument(
        '--absolute-threshold',
        dest='absolute_threshold',
        default=None,
        help='Optional absolute delta threshold for materiality evaluation (e.g. 100.0)',
    )
    compare_parser.add_argument(
        '--relative-threshold',
        dest='relative_threshold',
        type=float,
        default=None,
        help='Optional relative delta threshold for materiality evaluation (e.g. 0.05 for 5%%)',
    )
    compare_parser.add_argument(
        '--materiality-policy',
        dest='materiality_policy',
        default=None,
        help='Optional path to JSON file or JSON string defining materiality policy',
    )
    compare_parser.add_argument(
        '--relationship',
        dest='relationship',
        choices=['candidate', 'expected_agreement', 'intentional_variant', 'unrelated'],
        default='candidate',
        help="Definition relationship classification [default: candidate]",
    )
    compare_parser.add_argument(
        '--context-json',
        dest='context_json',
        default=None,
        help='Comparison context as JSON string',
    )
    compare_parser.add_argument(
        '--context', '--context-file',
        dest='context',
        default=None,
        help='Comparison context as JSON string or path to JSON file',
    )
    compare_parser.add_argument(
        '--database-url',
        dest='database_url',
        default=None,
        help='Store database URL (defaults to METRICGUARD_DATABASE_URL env)',
    )
    compare_parser.add_argument(
        '--data-dir',
        dest='data_dir',
        default=None,
        help='Data directory path (defaults to METRICGUARD_DATA_DIR env or data/)',
    )
    compare_parser.add_argument(
        '--output', '--json-output',
        dest='output',
        default=None,
        help='Write machine-readable report JSON to specified file path',
    )
    compare_parser.add_argument(
        '--markdown-output', '--md-output', '--markdown',
        dest='markdown_output',
        default=None,
        help='Write human-readable Markdown report to specified file path',
    )
    compare_parser.add_argument(
        '--format',
        dest='format',
        choices=['summary', 'json', 'quiet'],
        default='summary',
        help="Terminal output format: 'summary' (ASCII summary), 'json' (raw JSON), or 'quiet' [default: summary]",
    )
    compare_parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress terminal summary (shorthand for --format quiet)',
    )
    compare_parser.add_argument(
        '--json',
        action='store_true',
        help='Emit JSON to stdout (shorthand for --format json)',
    )

    diff_parser = sub.add_parser(
        'diff',
        help='Run a deterministic revision-level metric diff between two dbt revisions',
        description='Execute deterministic revision-level metric diff and coverage analysis on an immutable reference snapshot.',
    )
    diff_parser.add_argument(
        '--project-id', '--project',
        dest='project_id',
        required=False,
        default=None,
        help='Target project ID (defaults to METRICGUARD_PROJECT_ID environment variable)',
    )
    diff_parser.add_argument(
        '--baseline', '--baseline-revision', '--baseline-revision-id',
        dest='baseline',
        required=True,
        help='Baseline dbt import revision ID',
    )
    diff_parser.add_argument(
        '--target', '--target-revision', '--target-revision-id',
        dest='target',
        required=True,
        help='Target dbt import revision ID',
    )
    diff_parser.add_argument(
        '--snapshot', '--snapshot-id',
        dest='snapshot',
        required=True,
        help='Reference snapshot ID or selector (e.g. <uuid> or snapshot:<uuid>)',
    )
    diff_parser.add_argument(
        '--mode',
        dest='mode',
        choices=['advisory', 'enforcing'],
        default='advisory',
        help="Execution mode: 'advisory' (drift exits 0) or 'enforcing' (material drift exits 1) [default: advisory]",
    )
    diff_parser.add_argument(
        '--absolute-threshold',
        dest='absolute_threshold',
        default=None,
        help='Optional absolute delta threshold for materiality evaluation (e.g. 50.0)',
    )
    diff_parser.add_argument(
        '--relative-threshold',
        dest='relative_threshold',
        type=float,
        default=None,
        help='Optional relative delta threshold for materiality evaluation (e.g. 0.05 for 5%%)',
    )
    diff_parser.add_argument(
        '--materiality-policy',
        dest='materiality_policy',
        default=None,
        help='Optional path to JSON file or JSON string defining materiality policy',
    )
    diff_parser.add_argument(
        '--context-json',
        dest='context_json',
        default=None,
        help='Comparison context as JSON string',
    )
    diff_parser.add_argument(
        '--context', '--context-file',
        dest='context',
        default=None,
        help='Comparison context as JSON string or path to JSON file',
    )
    diff_parser.add_argument(
        '--database-url',
        dest='database_url',
        default=None,
        help='Store database URL (defaults to METRICGUARD_DATABASE_URL env)',
    )
    diff_parser.add_argument(
        '--data-dir',
        dest='data_dir',
        default=None,
        help='Data directory path (defaults to METRICGUARD_DATA_DIR env or data/)',
    )
    diff_parser.add_argument(
        '--output', '--json-output',
        dest='output',
        default=None,
        help='Write machine-readable report JSON to specified file path',
    )
    diff_parser.add_argument(
        '--markdown-output', '--md-output', '--markdown',
        dest='markdown_output',
        default=None,
        help='Write human-readable Markdown report to specified file path',
    )
    diff_parser.add_argument(
        '--format',
        dest='format',
        choices=['summary', 'json', 'quiet'],
        default='summary',
        help="Terminal output format: 'summary' (ASCII summary), 'json' (raw JSON), or 'quiet' [default: summary]",
    )
    diff_parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress terminal summary (shorthand for --format quiet)',
    )
    diff_parser.add_argument(
        '--json',
        action='store_true',
        help='Emit JSON to stdout (shorthand for --format json)',
    )

    validate_config_parser = sub.add_parser(
        'validate-config',
        aliases=['validate_config'],
        help='Validate metricguard.yml declarative repository configuration',
        description='Validate a metricguard.yml configuration file against schema, dbt manifest, and authoritative MetricGuard Store.',
    )
    validate_config_parser.add_argument(
        '--config', '-c',
        dest='config',
        default='metricguard.yml',
        help='Path to metricguard.yml file (default: metricguard.yml)',
    )
    validate_config_parser.add_argument(
        '--manifest', '-m',
        dest='manifest',
        default=None,
        help='Optional path to dbt manifest.json to verify dbt model resource bindings',
    )
    validate_config_parser.add_argument(
        '--schema-only',
        dest='schema_only',
        action='store_true',
        help='Perform offline structural, schema, and manifest validation without connecting to the Store',
    )
    validate_config_parser.add_argument(
        '--database-url',
        dest='database_url',
        default=None,
        help='MetricGuard Store database URL (defaults to METRICGUARD_DATABASE_URL or data/metricguard.db)',
    )
    validate_config_parser.add_argument(
        '--data-dir',
        dest='data_dir',
        default=None,
        help='MetricGuard data directory path (defaults to METRICGUARD_DATA_DIR env or data/)',
    )
    validate_config_parser.add_argument(
        '--provenance',
        dest='provenance',
        choices=['trusted_base', 'untrusted_candidate', 'local_user'],
        default='local_user',
        help='Configuration provenance / trust tier [default: local_user]',
    )
    validate_config_parser.add_argument(
        '--format',
        dest='format',
        choices=['summary', 'json', 'quiet'],
        default='summary',
        help="Terminal output format: 'summary' (human readable), 'json' (raw JSON), or 'quiet' [default: summary]",
    )
    validate_config_parser.add_argument(
        '--quiet', '-q',
        dest='quiet',
        action='store_true',
        help='Suppress terminal output (shorthand for --format quiet)',
    )
    validate_config_parser.add_argument(
        '--json',
        dest='json',
        action='store_true',
        help='Emit JSON to stdout (shorthand for --format json)',
    )
    validate_config_parser.add_argument(
        '--output', '-o',
        dest='output',
        default=None,
        help='Optional file path to write machine-readable validation result JSON',
    )

    diff_ci_parser = sub.add_parser(
        'diff-ci',
        aliases=['diff_ci'],
        help='Run turnkey provider-neutral CI/PR metric diff against manifests and metricguard.yml',
        description='Evaluate pull request dbt manifest changes against trusted repository configuration and authoritative MetricGuard Store.',
    )
    diff_ci_parser.add_argument(
        '--config', '-c',
        dest='config',
        default='metricguard.yml',
        help='Path to candidate metricguard.yml file (default: metricguard.yml)',
    )
    diff_ci_parser.add_argument(
        '--base-config',
        dest='base_config',
        default=None,
        help='Path to trusted base branch metricguard.yml file (defaults to --config if not specified)',
    )
    diff_ci_parser.add_argument(
        '--base-manifest',
        dest='base_manifest',
        required=True,
        help='Path to base dbt manifest.json (from target/main branch)',
    )
    diff_ci_parser.add_argument(
        '--target-manifest',
        dest='target_manifest',
        required=True,
        help='Path to candidate target dbt manifest.json (from PR branch)',
    )
    diff_ci_parser.add_argument(
        '--output-json', '-o', '--json-output',
        dest='output_json',
        default=None,
        help='Path to write machine-readable CI report JSON (e.g. metricguard_report.json)',
    )
    diff_ci_parser.add_argument(
        '--output-markdown', '--output-md', '--markdown-output',
        dest='output_markdown',
        default=None,
        help='Path to write pull request comment Markdown summary (e.g. metricguard_summary.md)',
    )
    diff_ci_parser.add_argument(
        '--database-url',
        dest='database_url',
        default=None,
        help='MetricGuard Store database URL (defaults to METRICGUARD_DATABASE_URL or data/metricguard.db)',
    )
    diff_ci_parser.add_argument(
        '--data-dir',
        dest='data_dir',
        default=None,
        help='MetricGuard data directory path (defaults to METRICGUARD_DATA_DIR env or data/)',
    )
    diff_ci_parser.add_argument(
        '--provenance',
        dest='provenance',
        choices=['trusted_base', 'untrusted_candidate', 'local_user'],
        help='Configuration provenance / trust tier for candidate config [default: local_user]',
    )
    diff_ci_parser.add_argument(
        '--materiality-policy',
        dest='materiality_policy',
        default=None,
        help='Optional path to JSON file or JSON string defining materiality policy thresholds',
    )
    diff_ci_parser.add_argument(
        '--format',
        dest='format',
        choices=['summary', 'json', 'quiet'],
        default='summary',
        help="Terminal output format: 'summary' (human readable), 'json' (raw JSON), or 'quiet' [default: summary]",
    )
    diff_ci_parser.add_argument(
        '--quiet', '-q',
        dest='quiet',
        action='store_true',
        help='Suppress terminal output (shorthand for --format quiet)',
    )
    diff_ci_parser.add_argument(
        '--json',
        dest='json',
        action='store_true',
        help='Emit JSON to stdout (shorthand for --format json)',
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as err:
        return err.code if isinstance(err.code, int) else 0
    except ArgumentParsingError as err:
        safe_msg = redact_api_key(str(err))
        print(f"Error: {safe_msg}", file=sys.stderr)
        return 3

    if args.command == 'diff':
        return _run_diff_cli(args, parser, argv)
    elif args.command in ('diff-ci', 'diff_ci'):
        return _run_diff_ci_cli(args, parser, argv)
    elif args.command in ('validate-config', 'validate_config'):
        return _run_validate_config_cli(args, parser, argv)
    elif args.command != 'compare':
        parser.print_help()
        return 3

    output_path = args.output
    markdown_path = args.markdown_output

    format_mode = 'summary'
    if args.quiet:
        format_mode = 'quiet'
    elif args.json or args.format == 'json':
        format_mode = 'json'
    elif args.format:
        format_mode = args.format

    # Never read API key from CLI args
    api_key = safe_env_api_key()
    project_id = args.project_id or os.environ.get('METRICGUARD_PROJECT_ID')

    if not project_id:
        print('Error: --project-id or METRICGUARD_PROJECT_ID is required', file=sys.stderr)
        return 3

    context_raw = args.context or args.context_json
    if not context_raw:
        print('Error: --context or --context-json is required', file=sys.stderr)
        return 3

    try:
        if os.path.isfile(context_raw):
            with open(context_raw, 'r', encoding='utf-8') as cp:
                context = json.load(cp)
        else:
            context = json.loads(context_raw)
        if not isinstance(context, dict):
            raise ValueError('Context must be a JSON object')
    except Exception as err:
        safe_err = redact_api_key(str(err))
        print(f'Error: invalid comparison context: {safe_err}', file=sys.stderr)
        return 3

    db_url = args.database_url or os.environ.get('METRICGUARD_DATABASE_URL')
    if not db_url:
        data_dir = Path(args.data_dir or os.environ.get('METRICGUARD_DATA_DIR', 'data'))
        db_url = f'sqlite:///{data_dir / "metricguard.db"}'

    try:
        store = Store(db_url)

        # Authenticate
        auth = authenticate_ci_caller(store, project_id, api_key)

        # Resolve inputs
        baseline_version = resolve_version(store, args.baseline, project_id)
        target_version = resolve_version(store, args.target, project_id)
        snapshot = resolve_snapshot(store, args.snapshot, project_id)

        # Build materiality policy
        mat_policy = None
        if args.materiality_policy:
            try:
                if os.path.isfile(args.materiality_policy):
                    with open(args.materiality_policy, 'r', encoding='utf-8') as mp:
                        mat_policy = json.load(mp)
                else:
                    mat_policy = json.loads(args.materiality_policy)
                if not isinstance(mat_policy, dict):
                    raise ValueError('Materiality policy must be a JSON object')
            except Exception as err:
                safe_err = redact_api_key(str(err))
                print(f'Error: invalid --materiality-policy: {safe_err}', file=sys.stderr)
                return 3

        if args.absolute_threshold is not None or args.relative_threshold is not None:
            if mat_policy is None:
                mat_policy = {}
            if args.absolute_threshold is not None:
                mat_policy['absolute_threshold'] = str(args.absolute_threshold)
            if args.relative_threshold is not None:
                mat_policy['relative_threshold'] = args.relative_threshold

        inputs = ResolvedInputs(
            project_id=project_id,
            baseline_version=baseline_version,
            target_version=target_version,
            snapshot=snapshot,
            context=context,
            mode=args.mode,
            materiality_policy=mat_policy,
            relationship=args.relationship,
        )

        report = execute_headless_comparison(store, inputs)

    except DomainError as err:
        exit_code = 2 if err.code in _REFUSAL_CODES else 3
        safe_msg = redact_api_key(err.message)
        error_report = {
            'schema': REPORT_SCHEMA,
            'version': REPORT_VERSION,
            'run_id': str(uuid4()),
            'project_id': project_id,
            'execution_mode': getattr(args, 'mode', 'advisory'),
            'outcome': CIOutcome.REFUSED.value if exit_code == 2 else CIOutcome.FAILED.value,
            'exit_code': exit_code,
            'diagnostic': safe_msg,
            'cli_version': CLI_VERSION,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'completed_at': datetime.now(timezone.utc).isoformat(),
        }
        if exit_code == 2:
            error_report['refusal'] = {'diagnostic': safe_msg}
        else:
            error_report['failure'] = {'diagnostic': safe_msg}

        try:
            _emit_report(
                error_report,
                output_path=output_path,
                markdown_path=markdown_path,
                format_mode=format_mode,
            )
        except OSError as emit_err:
            safe_emit_err = redact_api_key(str(emit_err))
            print(f"Error: failed to write output file: {safe_emit_err}", file=sys.stderr)
            return 3
        return exit_code

    except Exception as err:
        safe_msg = redact_api_key(str(err))
        error_report = {
            'schema': REPORT_SCHEMA,
            'version': REPORT_VERSION,
            'run_id': str(uuid4()),
            'project_id': project_id,
            'execution_mode': getattr(args, 'mode', 'advisory'),
            'outcome': CIOutcome.FAILED.value,
            'exit_code': 3,
            'diagnostic': safe_msg,
            'failure': {'diagnostic': safe_msg},
            'cli_version': CLI_VERSION,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'completed_at': datetime.now(timezone.utc).isoformat(),
        }
        try:
            _emit_report(
                error_report,
                output_path=output_path,
                markdown_path=markdown_path,
                format_mode=format_mode,
            )
        except OSError as emit_err:
            safe_emit_err = redact_api_key(str(emit_err))
            print(f"Error: failed to write output file: {safe_emit_err}", file=sys.stderr)
            return 3
        return 3

    try:
        _emit_report(
            report,
            output_path=output_path,
            markdown_path=markdown_path,
            format_mode=format_mode,
        )
    except OSError as err:
        safe_msg = redact_api_key(str(err))
        print(f"Error: failed to write output file: {safe_msg}", file=sys.stderr)
        return 3

    return report['exit_code']


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    sys.exit(run_cli())

