"""Deterministic coverage calculation, refusal aggregation, outcome determination,
and Markdown reporting for V5-03: Revision-Level Metric Diff Orchestration.
"""
from __future__ import annotations

from decimal import Decimal
import json
import re
from typing import Any

from services.cli import redact_api_key


def determine_diff_outcome_and_exit_code(
    mode: str,
    material_metrics_count: int,
    refused_metrics_count: int,
    operational_failure: bool = False,
) -> tuple[str, int]:
    """Determine the revision-level outcome and deterministic CI exit code.

    Exit Code Precedence:
    ---------------------
    3: Operational failure (missing snapshot, corrupted artifact, auth failure, etc.)
    2: Analytical refusal (MetricGuard refused quantitative analysis on one or more
       declared metrics). Refusal takes precedence over materiality and clean passes
       to ensure incomplete analytical coverage is never hidden.
    1: Enforcing mode with material drift (supported analysis completed in enforcing mode,
       0 refusals, and at least one metric exceeded materiality threshold).
    0: Clean completion or advisory drift (0 refusals, and either 0 material drift or
       execution was in advisory mode).

    Aggregation rule for mixtures (e.g. 3 supported/pass, 1 supported/material, 2 refused):
    Both advisory and enforcing modes exit with code 2. The outcome is recorded as
    'completed_with_material_drift_and_refusals', and the structured report retains
    the full breakdown of all analyzed metrics, material drift deltas, and exact refusal
    diagnostics.
    """
    if operational_failure:
        return 'operational_failure', 3

    if refused_metrics_count > 0 and material_metrics_count > 0:
        outcome = 'completed_with_material_drift_and_refusals'
        exit_code = 2
    elif refused_metrics_count > 0:
        outcome = 'completed_with_refusals'
        exit_code = 2
    elif material_metrics_count > 0:
        outcome = 'completed_with_material_drift'
        exit_code = 1 if mode == 'enforcing' else 0
    else:
        outcome = 'completed'
        exit_code = 0

    return outcome, exit_code


def compute_diff_coverage(change_set: dict, impact_report: dict) -> dict:
    """Compute deterministic coverage across structural, declared metric,

    importance-weighted, and observable structural signal views.
    """
    resources = change_set.get('resources', [])
    changed_resources = [r for r in resources if r.get('change_category') != 'unchanged']
    undeclared_list = impact_report.get('undeclared_resources', [])
    undeclared_uids = {u.get('unique_id') for u in undeclared_list}

    # 1. Structural / resource coverage
    total_changed = len(changed_resources)
    undeclared_count = len(undeclared_list)
    declared_linked_count = max(0, total_changed - undeclared_count)

    supported_executable_count = 0
    refused_res_count = 0
    refusal_reasons: dict[str, int] = {}

    for r in changed_resources:
        # Check target support state (and baseline if modified)
        new_state = r.get('new_support_state')
        old_state = r.get('old_support_state')
        is_executable = (new_state == 'executable') and (old_state in ('executable', None))

        if is_executable:
            supported_executable_count += 1
        else:
            refused_res_count += 1
            diags = r.get('new_diagnostics') or r.get('diagnostics') or r.get('old_diagnostics') or []
            reason = diags[0] if diags else f"Support state is '{new_state or old_state}'"
            refusal_reasons[reason] = refusal_reasons.get(reason, 0) + 1

    structural_coverage = {
        'changed_resources_count': total_changed,
        'declared_metric_linked_resources_count': declared_linked_count,
        'undeclared_changed_resources_count': undeclared_count,
        'supported_executable_resources_count': supported_executable_count,
        'refused_resources_count': refused_res_count,
        'refusal_reasons': refusal_reasons,
    }

    # 2. Declared Metric coverage
    metrics = impact_report.get('metrics', [])
    total_affected_metrics = len(metrics)
    analyzable_metrics = sum(1 for m in metrics if m.get('execution_status') == 'executed')
    refused_metrics = sum(1 for m in metrics if m.get('execution_status') == 'refused')

    if total_affected_metrics > 0:
        declared_coverage_pct = round((analyzable_metrics / total_affected_metrics) * 100, 2)
    else:
        declared_coverage_pct = 100.0

    declared_metrics_coverage = {
        'total_affected': total_affected_metrics,
        'analyzable': analyzable_metrics,
        'refused': refused_metrics,
        'coverage_pct': declared_coverage_pct,
    }

    # 3. Importance-weighted coverage (explicit only - NO inference)
    importance_tiers = ['critical', 'important', 'standard', 'unclassified']
    importance_weighted: dict[str, dict[str, Any]] = {}

    for tier in importance_tiers:
        tier_metrics = [m for m in metrics if m.get('importance') == tier]
        aff = len(tier_metrics)
        ana = sum(1 for m in tier_metrics if m.get('execution_status') == 'executed')
        ref = sum(1 for m in tier_metrics if m.get('execution_status') == 'refused')
        cov = round((ana / aff) * 100, 2) if aff > 0 else None
        importance_weighted[tier] = {
            'affected': aff,
            'analyzable': ana,
            'refused': ref,
            'coverage_pct': cov,
        }

    # 4. Observable structural signals (kept strictly separate from business importance)
    exposure_res: list[str] = []
    dbt_metric_res: list[str] = []
    tagged_res: list[str] = []

    for r in changed_resources:
        uid = r.get('unique_id', '')
        # Exposure signal
        downstream = []
        if r.get('new_lineage') and isinstance(r['new_lineage'], dict):
            downstream.extend(r['new_lineage'].get('downstream_nodes') or [])
        if r.get('old_lineage') and isinstance(r['old_lineage'], dict):
            downstream.extend(r['old_lineage'].get('downstream_nodes') or [])

        has_exposure = any(node.startswith('exposure.') for node in downstream)
        meta_tags = []
        if r.get('new_metadata') and isinstance(r['new_metadata'], dict):
            meta_tags.extend(r['new_metadata'].get('tags') or [])
        if r.get('old_metadata') and isinstance(r['old_metadata'], dict):
            meta_tags.extend(r['old_metadata'].get('tags') or [])

        if has_exposure or any('exposure' in t.lower() for t in meta_tags):
            if uid not in exposure_res:
                exposure_res.append(uid)

        # dbt metric references
        rtype = r.get('resource_type', '')
        if rtype in ('metric', 'semantic_model') or uid.startswith(('metric.', 'semantic_model.')) or any(node.startswith('metric.') for node in downstream):
            if uid not in dbt_metric_res:
                dbt_metric_res.append(uid)

        # Other metadata (tags)
        if meta_tags and uid not in tagged_res:
            tagged_res.append(uid)

    observable_signals = {
        'exposure_referenced_changed_resources': {
            'count': len(exposure_res),
            'resource_ids': sorted(exposure_res),
        },
        'explicit_dbt_metric_references': {
            'count': len(dbt_metric_res),
            'resource_ids': sorted(dbt_metric_res),
        },
        'other_observable_metadata': {
            'tagged_changed_resources': {
                'count': len(tagged_res),
                'resource_ids': sorted(tagged_res),
            }
        },
    }

    return {
        'structural': structural_coverage,
        'declared_metrics': declared_metrics_coverage,
        'importance_weighted': importance_weighted,
        'observable_structural_signals': observable_signals,
    }


def aggregate_diff_refusals(change_set: dict, impact_report: dict) -> list[dict]:
    """Aggregate all refusals across the revision diff with full diagnostics and stage."""
    refusals: list[dict] = []
    seen_keys: set[tuple[str | None, str]] = set()

    # 1. Refused declared metrics
    for m in impact_report.get('metrics', []):
        if m.get('execution_status') == 'refused':
            mid = m.get('metric_id')
            mname = m.get('display_name')
            reason = m.get('refusal_reason') or 'Quantitative analysis refused.'
            diag = m.get('diagnostics') or {}
            sup_state = m.get('support_state', 'unsupported')

            # Determine stage
            if 'binding' in reason.lower() or 'no active definition' in reason.lower():
                stage = 'definition_binding'
            elif 'snapshot' in reason.lower():
                stage = 'snapshot_gate'
            else:
                stage = 'support_gate'

            # Try to associate unique_id
            matched_uid = None
            for r in change_set.get('resources', []):
                if r.get('metric') and r['metric'].get('id') == mid:
                    matched_uid = r.get('unique_id')
                    break

            res_id = matched_uid or f"metric:{mid}"
            key = (mid, res_id)
            if key not in seen_keys:
                seen_keys.add(key)
                refusals.append({
                    'metric_id': mid,
                    'metric_display_name': mname,
                    'resource_id': res_id,
                    'support_state': sup_state,
                    'exact_diagnostic': reason,
                    'diagnostics': diag,
                    'refusal_stage': stage,
                })

    # 2. Refused candidate metrics
    for cm in impact_report.get('candidate_metrics', []):
        if cm.get('execution_status') == 'refused':
            mid = cm.get('metric_id')
            mname = cm.get('display_name')
            reason = cm.get('refusal_reason') or 'Candidate analysis refused.'
            diag = cm.get('diagnostics') or {}
            sup_state = cm.get('support_state', 'unsupported')
            res_id = f"candidate:{cm.get('candidate_definition_id')}"
            key = (mid, res_id)
            if key not in seen_keys:
                seen_keys.add(key)
                refusals.append({
                    'metric_id': mid,
                    'metric_display_name': mname,
                    'resource_id': res_id,
                    'support_state': sup_state,
                    'exact_diagnostic': reason,
                    'diagnostics': diag,
                    'refusal_stage': 'support_gate',
                })

    # 3. Refused changed resources (e.g. undeclared resources with CTE / unsupported constructs)
    for r in change_set.get('resources', []):
        if r.get('change_category') != 'unchanged':
            st = r.get('new_support_state') or r.get('old_support_state')
            if st in ('metadata_only', 'needs_context', 'unsupported'):
                uid = r.get('unique_id', '')
                key = (None, uid)
                if key not in seen_keys:
                    seen_keys.add(key)
                    diags = r.get('new_diagnostics') or r.get('diagnostics') or r.get('old_diagnostics') or []
                    reason = diags[0] if diags else f"Resource support state is '{st}'"
                    refusals.append({
                        'metric_id': None,
                        'metric_display_name': None,
                        'resource_id': uid,
                        'support_state': st,
                        'exact_diagnostic': reason,
                        'diagnostics': diags,
                        'refusal_stage': 'support_gate',
                    })

    return refusals


def format_metric_diff_markdown(report: dict) -> str:
    """Produce deterministic Markdown report for CI/PR review adhering to the 10 requirements."""
    lines: list[str] = []
    lines.append('# MetricGuard Revision Metric Diff Report')
    lines.append('')

    # Outcome badge
    outcome = report.get('overall_outcome', 'unknown')
    exit_code = report.get('exit_code', '?')
    mode = report.get('mode', 'advisory')

    status_labels = {
        'completed': 'PASSED (Clean Completion)',
        'completed_with_material_drift': 'MATERIAL DRIFT (Exceeded Threshold)' if mode == 'enforcing' else 'ADVISORY DRIFT (Completed with Material Drift)',
        'completed_with_refusals': 'REFUSED (Incomplete Coverage — Analysis Refused)',
        'completed_with_material_drift_and_refusals': 'REFUSED & MATERIAL (Material Drift and Analysis Refusals Present)',
        'operational_failure': 'FAILED (Operational Failure)',
    }
    badge = status_labels.get(outcome, f"{outcome.upper()} (Exit {exit_code})")

    # 1. Revision Comparison Identity
    lines.append('## 1. Revision Comparison Identity')
    lines.append('')
    lines.append('| Property | Value |')
    lines.append('| :--- | :--- |')
    lines.append(f'| **Overall Outcome** | `{outcome}` |')
    lines.append(f'| **Status Badge** | **{badge}** |')
    lines.append(f'| **Exit Code** | `{exit_code}` |')
    lines.append(f'| **Execution Mode** | `{mode}` |')
    lines.append(f'| **Project ID** | `{report.get("project_id")}` |')
    lines.append(f'| **Baseline Revision** | `{report.get("baseline_revision_id")}` |')
    lines.append(f'| **Target Revision** | `{report.get("target_revision_id")}` |')
    if report.get('baseline_manifest_hash'):
        lines.append(f'| **Baseline Manifest Hash** | `{report["baseline_manifest_hash"]}` |')
    if report.get('target_manifest_hash'):
        lines.append(f'| **Target Manifest Hash** | `{report["target_manifest_hash"]}` |')
    if report.get('change_set_id'):
        lines.append(f'| **Change Set ID** | `{report["change_set_id"]}` |')
    if report.get('request_hash'):
        lines.append(f'| **Request Hash** | `{report["request_hash"]}` |')

    src_meta = report.get('source_metadata') or {}
    b_src = src_meta.get('baseline') or {}
    t_src = src_meta.get('target') or {}
    b_git = b_src.get('git_sha', 'n/a')
    t_git = t_src.get('git_sha', 'n/a')
    lines.append(f'| **Git Provenance** | `{b_git}` &rarr; `{t_git}` |')
    lines.append('')

    # 2. Pinned Reference Snapshot
    lines.append('## 2. Reference Snapshot')
    lines.append('')
    lines.append('| Property | Value |')
    lines.append('| :--- | :--- |')
    lines.append(f'| **Snapshot ID** | `{report.get("snapshot_id")}` |')
    lines.append(f'| **Snapshot Manifest Hash** | `{report.get("snapshot_hash")}` |')
    lines.append('| **PostgreSQL Source Query** | *None (Pinned DuckDB Snapshot Isolation)* |')
    lines.append('')

    # 3. Changed-Resource Summary
    counts = report.get('counts', {})
    lines.append('## 3. Changed-Resource Summary')
    lines.append('')
    lines.append('| Category | Count |')
    lines.append('| :--- | :--- |')
    lines.append(f'| **Total Changed Resources** | {counts.get("total_changed_resources", 0)} |')
    lines.append(f'| **Unchanged Resources** | {counts.get("unchanged_resources", 0)} |')
    lines.append(f'| **Declared Metric-Linked Resources** | {counts.get("total_declared_metrics_affected", 0)} |')
    lines.append(f'| **Undeclared Changed Resources** | {counts.get("undeclared_changed_resources", 0)} |')
    lines.append(f'| **Successfully Analyzed Metrics** | {counts.get("successfully_analyzed_metrics", 0)} |')
    lines.append(f'| **Refused Metrics** | {counts.get("refused_metrics", 0)} |')
    lines.append(f'| **Material Metrics** | {counts.get("material_metrics", 0)} |')
    lines.append('')

    # 4. Declared Metric Coverage
    cov = report.get('coverage', {})
    dec_cov = cov.get('declared_metrics', {})
    lines.append('## 4. Declared Metric Coverage')
    lines.append('')
    lines.append('| Metric Coverage Metric | Value |')
    lines.append('| :--- | :--- |')
    lines.append(f'| **Total Affected Declared Metrics** | {dec_cov.get("total_affected", 0)} |')
    lines.append(f'| **Analyzable Metrics** | {dec_cov.get("analyzable", 0)} |')
    lines.append(f'| **Refused Metrics** | {dec_cov.get("refused", 0)} |')
    cov_val = dec_cov.get("coverage_pct")
    cov_str = f"{cov_val:.2f}%" if cov_val is not None else "N/A"
    lines.append(f'| **Analytical Coverage %** | **{cov_str}** |')
    lines.append('')

    # 5. Importance-Weighted Coverage
    imp_cov = cov.get('importance_weighted', {})
    lines.append('## 5. Importance-Weighted Coverage (Explicit Declarations Only)')
    lines.append('')
    lines.append('| Importance | Affected | Analyzable | Refused | Coverage % |')
    lines.append('| :--- | :--- | :--- | :--- | :--- |')
    for tier in ('critical', 'important', 'standard', 'unclassified'):
        t_data = imp_cov.get(tier, {})
        t_aff = t_data.get('affected', 0)
        t_ana = t_data.get('analyzable', 0)
        t_ref = t_data.get('refused', 0)
        t_pct = t_data.get('coverage_pct')
        pct_display = f"{t_pct:.2f}%" if t_pct is not None else "N/A"
        lines.append(f'| **{tier.capitalize()}** | {t_aff} | {t_ana} | {t_ref} | {pct_display} |')
    lines.append('')

    # 6. Analyzed Metrics & Exact Impact
    lines.append('## 6. Analyzed Metrics & Exact Impact')
    lines.append('')
    metrics = report.get('metrics', [])
    analyzed = [m for m in metrics if m.get('execution_status') == 'executed']
    if analyzed:
        lines.append('| Metric | Importance | Base Value | Target Value | Exact Delta | Rel Change % | Material? |')
        lines.append('| :--- | :--- | :--- | :--- | :--- | :--- | :--- |')
        for m in analyzed:
            mname = m.get('display_name', m.get('metric_id'))
            imp = m.get('importance', 'unclassified')
            bval = m.get('baseline_value', '0')
            tval = m.get('target_value', '0')
            edelta = m.get('exact_delta', '0')
            rchg = m.get('relative_change_percent') or '0.0%'
            mat = m.get('materiality', {})
            ismat = '**YES**' if mat.get('is_material') else 'No'
            lines.append(f'| **{mname}** | `{imp}` | {bval} | {tval} | `{edelta}` | {rchg} | {ismat} |')
    else:
        lines.append('*No declared business metrics were quantitatively analyzed.*')
    lines.append('')

    # 7. Approved Baseline Drift vs Incremental PR Impact
    lines.append('## 7. Approved Baseline Drift vs Incremental PR Impact')
    lines.append('')
    has_appr = any(m.get('has_approved_baseline') for m in analyzed)
    if has_appr:
        lines.append('| Metric | Approved Baseline Impact ($V_{appr} \\to V_{tgt}$) | Incremental PR Impact ($V_{base} \\to V_{tgt}$) | Drift Amnesia Guard |')
        lines.append('| :--- | :--- | :--- | :--- |')
        for m in analyzed:
            if m.get('has_approved_baseline'):
                mname = m.get('display_name')
                appr_imp = m.get('approved_baseline_impact') or {}
                incr_imp = m.get('incremental_impact') or {}
                appr_str = f"&Delta; `{appr_imp.get('exact_delta')}` ({appr_imp.get('relative_change_percent', '0%')})"
                incr_str = f"&Delta; `{incr_imp.get('exact_delta')}` ({incr_imp.get('relative_change_percent', '0%')})"
                lines.append(f'| **{mname}** | {appr_str} | {incr_str} | Evaluated independently |')
    else:
        lines.append('*No evaluated metrics had an approved business baseline; fallback to baseline revision was utilized.*')
    lines.append('')

    # 8. Refused Metrics & Resources
    refusals = report.get('refusals', [])
    lines.append('## 8. Refused Metrics and Resources')
    lines.append('')
    if refusals:
        lines.append('| Target / Metric | Resource ID | Support State | Stage | Diagnostic Reason |')
        lines.append('| :--- | :--- | :--- | :--- | :--- |')
        for ref in refusals:
            target_label = f"**{ref['metric_display_name']}**" if ref.get('metric_display_name') else '*(undeclared)*'
            rid = ref.get('resource_id', 'unknown')
            st = ref.get('support_state', 'unsupported')
            stage = ref.get('refusal_stage', 'gate')
            diag = ref.get('exact_diagnostic', '')
            lines.append(f'| {target_label} | `{rid}` | `{st}` | `{stage}` | {diag} |')
    else:
        lines.append('*No analytical refusals recorded across this revision diff.*')
    lines.append('')

    # 9. Undeclared Changed Resources
    undeclared = report.get('undeclared_resources', [])
    lines.append('## 9. Undeclared Changed Resources')
    lines.append('')
    if undeclared:
        lines.append('| Resource Unique ID | Resource Type | Change Category | Dimensions | Status |')
        lines.append('| :--- | :--- | :--- | :--- | :--- |')
        for u in undeclared:
            uid = u.get('unique_id')
            rtype = u.get('resource_type', 'model')
            ccat = u.get('change_category', 'modified')
            cdims = ', '.join(u.get('change_dimensions', []))
            reason = u.get('reason', 'UNDECLARED_METRIC')
            lines.append(f'| `{uid}` | `{rtype}` | `{ccat}` | `{cdims}` | `{reason}` |')
    else:
        lines.append('*No undeclared dbt resources were changed.*')
    lines.append('')

    # 10. Investigation Interoperability
    lines.append('## 10. Investigation Interoperability & Evidence References')
    lines.append('')
    impact_id = report.get('impact_report_id')
    pid = report.get('project_id')
    if analyzed:
        lines.append('Analyzed metrics can be promoted to persistent investigations without recomputation:')
        for m in analyzed:
            mname = m.get('display_name')
            run_id = m.get('comparison_run_id')
            lines.append(f'- **{mname}**: Run ID `{run_id}` | Promote via `POST /api/v1/projects/{pid}/dbt/metric-impacts/{impact_id}/investigations`')
    else:
        lines.append('*No executable metrics available for investigation promotion.*')
    lines.append('')

    return redact_api_key('\n'.join(lines))
