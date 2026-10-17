import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  buildVersionPathSteps,
  classifyDiffOutcome,
  formatDelta,
  formatMaterialityBadge,
  formatRevisionLabel,
  humanizeRefusalReason,
  sortAffectedMetricsByActionRelevance,
} from './changesUtils.ts';

test('formatRevisionLabel avoids raw internal IDs when git_sha or branch is present', () => {
  const lbl1 = formatRevisionLabel('dbt-rev-1234567890', {
    git_sha: '8d41a3fc99',
    branch: 'main',
  });
  assert.equal(lbl1, 'main (8d41a3f)');

  const lbl2 = formatRevisionLabel('dbt-rev-1234567890', {
    git_sha: '92bc71eabc',
  });
  assert.equal(lbl2, '92bc71e');

  const lbl3 = formatRevisionLabel('dbt-rev-1234567890', {
    branch: 'feature/revenue',
  });
  assert.equal(lbl3, 'feature/revenue');

  const lbl4 = formatRevisionLabel('dbt-rev-1234567890');
  assert.equal(lbl4, 'dbt-rev-…');
});

test('sortAffectedMetricsByActionRelevance orders strictly by action priority', () => {
  const metrics = [
    {
      metric_id: 'm-unchanged',
      display_name: 'Total Units (Unchanged)',
      importance: 'standard',
      comparison_type: 'active',
      has_approved_baseline: true,
      reference_snapshot_id: 's1',
      support_state: 'executable',
      execution_status: 'executed',
      exact_delta: '0.00',
      materiality: { exceeded: false },
    },
    {
      metric_id: 'm-crit-mat',
      display_name: 'Revenue (Critical Material)',
      importance: 'critical',
      comparison_type: 'active',
      has_approved_baseline: true,
      reference_snapshot_id: 's1',
      support_state: 'executable',
      execution_status: 'executed',
      exact_delta: '-50000.00',
      materiality: { exceeded: true },
    },
    {
      metric_id: 'm-imp-mat',
      display_name: 'Order Count (Important Material)',
      importance: 'important',
      comparison_type: 'active',
      has_approved_baseline: true,
      reference_snapshot_id: 's1',
      support_state: 'executable',
      execution_status: 'executed',
      exact_delta: '-120.00',
      materiality: { exceeded: true },
    },
    {
      metric_id: 'm-refused-crit',
      display_name: 'Customer LTV (Critical Refused)',
      importance: 'critical',
      comparison_type: 'active',
      has_approved_baseline: true,
      reference_snapshot_id: 's1',
      support_state: 'metadata_only',
      execution_status: 'refused',
      refusal_reason: 'JOIN structure not supported',
    },
    {
      metric_id: 'm-std-mat',
      display_name: 'Shipping Cost (Standard Material)',
      importance: 'standard',
      comparison_type: 'active',
      has_approved_baseline: true,
      reference_snapshot_id: 's1',
      support_state: 'executable',
      execution_status: 'executed',
      exact_delta: '-1500.00',
      materiality: { exceeded: true },
    },
    {
      metric_id: 'm-non-mat',
      display_name: 'Tax Amount (Non-material Analyzed)',
      importance: 'standard',
      comparison_type: 'active',
      has_approved_baseline: true,
      reference_snapshot_id: 's1',
      support_state: 'executable',
      execution_status: 'executed',
      exact_delta: '-2.50',
      materiality: { exceeded: false },
    },
  ];

  const sorted = sortAffectedMetricsByActionRelevance(metrics);
  const names = sorted.map((m) => m.display_name);

  // Expected order:
  // 1. Critical Material
  // 2. Important Material
  // 3. Other Material (Standard Material)
  // 4. Critical Refused
  // 5. Non-material analyzed
  // 6. Unchanged
  assert.equal(names[0], 'Revenue (Critical Material)');
  assert.equal(names[1], 'Order Count (Important Material)');
  assert.equal(names[2], 'Shipping Cost (Standard Material)');
  assert.equal(names[3], 'Customer LTV (Critical Refused)');
  assert.equal(names[4], 'Tax Amount (Non-material Analyzed)');
  assert.equal(names[5], 'Total Units (Unchanged)');
});

test('buildVersionPathSteps handles intermediate branch states and missing approved baselines', () => {
  // Case A: Full 3-point path: Approved -> Baseline Revision -> Target Revision
  const m1 = {
    metric_id: 'm1',
    display_name: 'Revenue',
    importance: 'critical',
    comparison_type: 'active',
    has_approved_baseline: true,
    approved_version_id: 'def-v18-approved',
    baseline_revision_version_id: 'def-v20-base-branch',
    target_version_id: 'def-v21-candidate',
    reference_snapshot_id: 'snap-1',
    support_state: 'executable',
    execution_status: 'executed',
    approved_baseline_impact: {
      execution_status: 'executed',
      baseline_value: '1985000.00',
      target_value: '1935000.00',
      exact_delta: '-50000.00',
      absolute_delta: '50000.00',
      relative_change_percent: '-2.52',
    },
    incremental_impact: {
      execution_status: 'executed',
      baseline_value: '1965000.00',
      target_value: '1935000.00',
      exact_delta: '-30000.00',
      absolute_delta: '30000.00',
      relative_change_percent: '-1.53',
    },
  };

  const steps1 = buildVersionPathSteps(m1);
  assert.equal(steps1.length, 3);
  assert.equal(steps1[0].role, 'approved');
  assert.equal(steps1[0].value, '1985000.00');
  assert.equal(steps1[0].isAvailable, true);

  assert.equal(steps1[1].role, 'baseline');
  assert.equal(steps1[1].value, '1965000.00');
  assert.equal(steps1[1].isAvailable, true);

  assert.equal(steps1[2].role, 'target');
  assert.equal(steps1[2].value, '1935000.00');
  assert.equal(steps1[2].isAvailable, true);

  // Case B: No approved baseline exists
  const m2 = {
    metric_id: 'm2',
    display_name: 'New Metric',
    importance: 'standard',
    comparison_type: 'active',
    has_approved_baseline: false,
    approved_version_id: null,
    baseline_revision_version_id: 'def-v1',
    target_version_id: 'def-v2',
    reference_snapshot_id: 'snap-1',
    support_state: 'executable',
    execution_status: 'executed',
    incremental_impact: {
      execution_status: 'executed',
      baseline_value: '500.00',
      target_value: '600.00',
      exact_delta: '100.00',
      absolute_delta: '100.00',
      relative_change_percent: '20.00',
    },
  };

  const steps2 = buildVersionPathSteps(m2);
  assert.equal(steps2[0].role, 'approved');
  assert.equal(steps2[0].isAvailable, false);
  assert.equal(steps2[0].value, null);
  assert.equal(steps2[1].role, 'baseline');
  assert.equal(steps2[1].value, '500.00');
  assert.equal(steps2[2].role, 'target');
  assert.equal(steps2[2].value, '600.00');
});

test('formatMaterialityBadge classifies material drift in red and within threshold in blue', () => {
  const mMaterial = {
    metric_id: 'm1',
    display_name: 'Revenue',
    importance: 'critical',
    comparison_type: 'active',
    has_approved_baseline: true,
    reference_snapshot_id: 's1',
    support_state: 'executable',
    execution_status: 'executed',
    materiality: { exceeded: true, reason: 'Absolute delta 50000.00 exceeded threshold 50.00' },
  };
  const b1 = formatMaterialityBadge(mMaterial);
  assert.equal(b1.label, 'Material drift');
  assert.equal(b1.className, 'critical');

  const mWithin = {
    metric_id: 'm2',
    display_name: 'Tax',
    importance: 'standard',
    comparison_type: 'active',
    has_approved_baseline: true,
    reference_snapshot_id: 's1',
    support_state: 'executable',
    execution_status: 'executed',
    materiality: { exceeded: false },
  };
  const b2 = formatMaterialityBadge(mWithin);
  assert.equal(b2.label, 'Within threshold');
  assert.equal(b2.className, 'attention');

  const mRefused = {
    metric_id: 'm3',
    display_name: 'LTV',
    importance: 'important',
    comparison_type: 'active',
    has_approved_baseline: true,
    reference_snapshot_id: 's1',
    support_state: 'metadata_only',
    execution_status: 'refused',
  };
  const b3 = formatMaterialityBadge(mRefused);
  assert.equal(b3.label, 'Not evaluated (Refused)');
  assert.equal(b3.className, 'warning');
});

test('humanizeRefusalReason returns clear guidance and never confuses refusals with zero drift', () => {
  const r1 = humanizeRefusalReason('Common Table Expressions (WITH ...) are not supported in MetricGuard IR.');
  assert.match(r1, /Common Table Expressions/);

  const r2 = humanizeRefusalReason('JOIN structure not supported for deterministic impact analysis.');
  assert.match(r2, /JOIN structure not supported/);

  const r3 = humanizeRefusalReason('Metric has no active definition binding.');
  assert.match(r3, /Metric has no active definition binding/);

  // Refusal must never be an empty string or 'No change'
  assert.notEqual(r1, '');
  assert.notEqual(r1, 'No change');
});

test('classifyDiffOutcome respects CI exit code precedence and mixture rules', () => {
  // Mixture of material drift and refusal must result in code 2 precedence
  const mixtureDiff = {
    schema_version: 'metricguard_metric_diff_v1',
    id: 'diff-mix',
    project_id: 'local-retail',
    baseline_revision_id: 'b1',
    target_revision_id: 't1',
    baseline_manifest_hash: 'h1',
    target_manifest_hash: 'h2',
    snapshot_id: 's1',
    snapshot_hash: 'sh1',
    source_metadata: { baseline: {}, target: {} },
    change_set_id: 'cs1',
    change_set_hash: 'csh1',
    impact_report_id: 'imp1',
    mode: 'advisory',
    overall_outcome: 'completed_with_material_drift_and_refusals',
    exit_code: 2,
    counts: {
      total_changed_resources: 4,
      unchanged_resources: 0,
      total_declared_metrics_affected: 3,
      successfully_analyzed_metrics: 2,
      refused_metrics: 1,
      undeclared_changed_resources: 1,
      material_metrics: 1,
    },
    coverage: {},
    refusals: [],
    metrics: [],
    undeclared_resources: [],
    created_at: '2026-09-26T08:00:00Z',
  };

  const outcome = classifyDiffOutcome(mixtureDiff);
  assert.equal(outcome.label, 'Material drift & Refused');
  assert.equal(outcome.className, 'critical');
  assert.match(outcome.description, /Exit code 2 precedence/);
});

test('formatDelta preserves exact currency symbol and decimal formatting', () => {
  assert.equal(formatDelta('-50000.00', 'INR'), '−₹50,000.00');
  assert.equal(formatDelta('1234.50', 'INR'), '+₹1,234.50');
  assert.equal(formatDelta('0.00', 'INR'), '₹0.00');
  assert.equal(formatDelta(null, 'INR'), 'Unavailable');
});

test('Zero-green design system constraint verification for changes', () => {
  // Verify that badge classNames strictly use attention (blue), critical (red), warning (amber), neutral
  const m1 = {
    metric_id: 'm1',
    display_name: 'Test',
    importance: 'critical',
    comparison_type: 'active',
    has_approved_baseline: true,
    reference_snapshot_id: 's1',
    support_state: 'executable',
    execution_status: 'executed',
    materiality: { exceeded: false },
  };
  const badge = formatMaterialityBadge(m1);
  assert.equal(badge.className, 'attention'); // Cobalt blue, NOT green!
  assert.notEqual(badge.className, 'success');
  assert.notEqual(badge.className, 'green');
});
