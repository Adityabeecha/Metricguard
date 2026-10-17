/**
 * Pure helper utilities for V5.2-04 Changes / Metric Diff Workbench.
 * Implements deterministic sorting, version path resolution, materiality translation,
 * refusal categorization, coverage aggregation, and CI exit code explanation.
 */

import type { AffectedMetricDiffItem, MetricDiffReport } from './api';

export interface VersionPathStep {
  label: string;
  role: 'approved' | 'baseline' | 'target';
  versionId: string | null;
  value: string | null;
  isAvailable: boolean;
}

export interface MetricOutcomeBadge {
  label: string;
  className: 'attention' | 'critical' | 'warning' | 'neutral';
  description: string;
}

/**
 * 04.4 Affected Metrics Sorting Priority:
 * 1. critical + material
 * 2. important + material
 * 3. other material
 * 4. refused critical/important
 * 5. non-material analyzed
 * 6. unchanged
 *
 * Explicit Metric.importance ONLY. Zero inference.
 */
export function sortAffectedMetricsByActionRelevance(
  metrics: AffectedMetricDiffItem[]
): AffectedMetricDiffItem[] {
  const getRank = (m: AffectedMetricDiffItem): number => {
    const isMaterial = Boolean(m.materiality?.is_material ?? m.materiality?.exceeded);
    const isRefused = m.execution_status === 'refused';
    const importance = m.importance || 'unclassified';
    const isCritical = importance === 'critical';
    const isImportant = importance === 'important';

    const hasZeroDelta =
      m.exact_delta === '0' ||
      m.exact_delta === '0.00' ||
      m.exact_delta === '0.0' ||
      (m.absolute_delta === '0' || m.absolute_delta === '0.00');

    // 1. critical + material
    if (isCritical && isMaterial && !isRefused) return 1;
    // 2. important + material
    if (isImportant && isMaterial && !isRefused) return 2;
    // 3. other material (standard/unclassified + material)
    if (isMaterial && !isRefused) return 3;
    // 4. refused critical/important
    if (isRefused && (isCritical || isImportant)) return 4;
    // other refused
    if (isRefused) return 5;
    // 6. unchanged (analyzed with exact zero delta)
    if (hasZeroDelta) return 7;
    // 5. non-material analyzed with non-zero delta
    return 6;
  };

  return [...metrics].sort((a, b) => {
    const rankA = getRank(a);
    const rankB = getRank(b);
    if (rankA !== rankB) return rankA - rankB;
    return a.display_name.localeCompare(b.display_name);
  });
}

/**
 * 04.5 Version Path Step Resolution:
 * Approved -> Current branch / Baseline revision -> Candidate / Target revision
 * Only renders intermediate states if the backend actually knows them.
 * If no approved baseline exists, explicitly marks approved baseline as unavailable.
 */
export function buildVersionPathSteps(m: AffectedMetricDiffItem): VersionPathStep[] {
  const steps: VersionPathStep[] = [];

  // Step 1: Approved Baseline
  if (m.has_approved_baseline && m.approved_version_id) {
    const val = m.approved_baseline_impact?.baseline_value ?? m.baseline_value ?? null;
    steps.push({
      label: `Approved (${m.approved_version_id.slice(0, 10)})`,
      role: 'approved',
      versionId: m.approved_version_id,
      value: val,
      isAvailable: true,
    });
  } else {
    steps.push({
      label: 'Approved baseline',
      role: 'approved',
      versionId: null,
      value: null,
      isAvailable: false,
    });
  }

  // Step 2: Baseline Revision (Intermediate if distinct from approved)
  if (
    m.baseline_revision_version_id &&
    m.baseline_revision_version_id !== m.approved_version_id
  ) {
    const baseVal = m.incremental_impact?.baseline_value ?? null;
    steps.push({
      label: `Baseline revision (${m.baseline_revision_version_id.slice(0, 10)})`,
      role: 'baseline',
      versionId: m.baseline_revision_version_id,
      value: baseVal,
      isAvailable: true,
    });
  }

  // Step 3: Target Revision
  const targetVal =
    m.incremental_impact?.target_value ??
    m.approved_baseline_impact?.target_value ??
    m.target_value ??
    null;
  steps.push({
    label: m.target_version_id
      ? `Target revision (${m.target_version_id.slice(0, 10)})`
      : 'Target revision',
    role: 'target',
    versionId: m.target_version_id || null,
    value: targetVal,
    isAvailable: m.execution_status === 'executed',
  });

  return steps;
}

/**
 * 04.7 Materiality Formatting:
 * Backend-derived facts only. Never raw "Material = false".
 */
export function formatMaterialityBadge(
  m: AffectedMetricDiffItem
): { label: string; className: 'critical' | 'attention' | 'warning' | 'neutral' } {
  if (m.execution_status === 'refused') {
    return { label: 'Not evaluated (Refused)', className: 'warning' };
  }
  if (!m.materiality) {
    return { label: 'Advisory (No threshold)', className: 'attention' };
  }
  const isMaterial = Boolean(m.materiality.is_material ?? m.materiality.exceeded);
  if (isMaterial) {
    return { label: 'Material drift', className: 'critical' };
  }
  return { label: 'Within threshold', className: 'attention' };
}

/**
 * 04.8 Refusal Humanizer:
 * Refusal is NOT pass, fail, zero impact, or unchanged. Amber styling.
 */
export function humanizeRefusalReason(rawReason?: string | null): string {
  if (!rawReason) return 'Quantitative impact analysis refused for this definition.';
  const lower = rawReason.toLowerCase();
  if (lower.includes('cte') || lower.includes('with clause') || lower.includes('common table expression')) {
    return 'Common Table Expressions (WITH ...) are not supported in MetricGuard IR.';
  }
  if (lower.includes('join')) {
    return 'JOIN structure not supported for deterministic impact analysis.';
  }
  if (lower.includes('binding') || lower.includes('no active definition')) {
    return 'Metric has no active definition binding registered in this workspace.';
  }
  if (lower.includes('removed') || lower.includes('resource removed')) {
    return 'Target definition version unavailable (resource removed in target revision).';
  }
  if (lower.includes('snapshot')) {
    return 'Reference snapshot does not satisfy required table or schema requirements.';
  }
  return rawReason;
}

/**
 * 04.12 CI Outcome & Exit Code Translation:
 * 0: Analyzed / Advisory
 * 1: Material change (enforcing)
 * 2: Analytical refusal (takes precedence)
 * 3: Operational error
 */
export function classifyDiffOutcome(diff: MetricDiffReport): MetricOutcomeBadge {
  const code = diff.exit_code;
  const outcome = diff.overall_outcome;

  if (outcome === 'operational_failure' || code === 3) {
    return {
      label: 'Operational error',
      className: 'warning',
      description: 'Diff failed due to missing snapshot, corrupted artifact, or environment configuration.',
    };
  }

  if (
    outcome === 'completed_with_material_drift_and_refusals' ||
    (code === 2 && diff.counts.material_metrics > 0)
  ) {
    return {
      label: 'Material drift & Refused',
      className: 'critical',
      description: 'Revision contains both material metric drift and refused analytical definitions (Exit code 2 precedence).',
    };
  }

  if (outcome === 'completed_with_refusals' || code === 2) {
    return {
      label: 'Refused',
      className: 'warning',
      description: 'Quantitative analysis was refused on one or more declared metrics.',
    };
  }

  if (outcome === 'completed_with_material_drift' || code === 1) {
    return {
      label: 'Material change',
      className: 'critical',
      description: 'One or more declared metrics exceeded the configured materiality threshold.',
    };
  }

  return {
    label: 'Analyzed',
    className: 'attention',
    description: 'All declared metrics analyzed successfully within acceptable variance.',
  };
}

/**
 * Formats a clean revision label avoiding raw internal IDs when git/branch is known.
 */
export function formatRevisionLabel(
  revisionId: string,
  sourceRevision?: { git_sha?: string; branch?: string; [k: string]: unknown }
): string {
  if (sourceRevision?.branch && sourceRevision?.git_sha) {
    return `${sourceRevision.branch} (${sourceRevision.git_sha.slice(0, 7)})`;
  }
  if (sourceRevision?.git_sha) {
    return sourceRevision.git_sha.slice(0, 7);
  }
  if (sourceRevision?.branch) {
    return sourceRevision.branch;
  }
  return revisionId.length > 12 ? `${revisionId.slice(0, 8)}…` : revisionId;
}

/**
 * Formats exact decimal delta with sign and currency.
 */
export function formatDelta(
  val: string | null | undefined,
  currency: string = ''
): string {
  if (val == null) return 'Unavailable';
  const num = Number(val);
  const prefix = !currency ? '' : currency === 'INR' ? '₹' : `${currency} `;
  if (!Number.isFinite(num)) return `${prefix}${val}`;
  const sign = num > 0 ? '+' : num < 0 ? '−' : '';
  const absFormatted = new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }).format(Math.abs(num));
  return `${sign}${prefix}${absFormatted}`;
}
