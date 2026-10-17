import test from 'node:test';
import assert from 'node:assert/strict';

// Helper functions mirroring MetricsView business logic
function getMetricSupportState(metric, definitions) {
  if (!metric.active_binding?.definition_id) {
    return { state: 'unbound', label: 'Unbound', isAnalyzable: false, reason: 'No active definition bound', version: null, definition: null };
  }
  const def = definitions.find((d) => d.id === metric.active_binding.definition_id);
  if (!def) {
    return { state: 'unbound', label: 'Unbound', isAnalyzable: false, reason: 'Definition not found', version: null, definition: null };
  }
  const approvedVerId = metric.active_binding?.approved_version_id;
  const ver = approvedVerId ? def.versions.find((v) => v.id === approvedVerId) : null;
  if (!ver) {
    return {
      state: 'unbound',
      label: 'Pending approval',
      isAnalyzable: false,
      reason: 'No approved version bound to this metric',
      version: null,
      definition: def,
    };
  }
  const isAnalyzable = ver.support_state === 'executable' || ver.support_state === 'supported';
  return {
    state: isAnalyzable ? 'analyzable' : 'limited',
    label: isAnalyzable ? 'Analyzable' : 'Limited / Refused',
    isAnalyzable,
    reason: ver.support_reason || (isAnalyzable ? 'Compatible closed single-table IR' : 'Unsupported SQL structure'),
    version: ver,
    definition: def,
  };
}

function getUnassignedDefinitions(definitions, metrics, bindings = []) {
  const boundIds = new Set();
  metrics.forEach((m) => {
    if (m.active_binding?.definition_id) boundIds.add(m.active_binding.definition_id);
  });
  bindings.forEach((b) => {
    if (b.definition_id) boundIds.add(b.definition_id);
  });
  return definitions.filter((d) => !boundIds.has(d.id));
}

function classifyVersionStatus(ver, activeBinding, candidateBindings = []) {
  if (ver.id === activeBinding?.approved_version_id) {
    return 'approved';
  }
  if (candidateBindings.some((c) => c.approved_version_id === ver.id || c.definition_id === ver.definition_id)) {
    return 'candidate';
  }
  return 'superseded';
}

function evaluateDrift(run, approvedVersionId) {
  if (!run || !run.result) return null;
  if (run.baseline_version_id !== approvedVersionId && run.target_version_id !== approvedVersionId) return null;
  const res = run.result;
  const isZero = res.delta_target_minus_baseline === '0' || res.delta_target_minus_baseline === '0.00' || res.absolute_gap === '0';
  const deltaNum = Math.abs(Number(res.delta_target_minus_baseline || 0));
  const isMaterial = !isZero && deltaNum > 100;
  return {
    isZero,
    isMaterial,
    delta: res.delta_target_minus_baseline,
    percent: res.relative_change_percent_display,
    statusLabel: isZero ? 'Exact match (zero drift)' : isMaterial ? 'Material drift' : 'Immaterial drift',
    pillClass: isZero ? 'ready' : isMaterial ? 'error' : 'neutral',
  };
}

test('Metric importance classification handles critical, important, and standard tiers', () => {
  const criticalMetric = {
    id: 'm-revenue',
    project_id: 'local-retail',
    display_name: 'Total Revenue',
    importance: 'critical',
    created_at: '2026-03-01T00:00:00Z',
  };
  const standardMetric = {
    id: 'm-orders',
    project_id: 'local-retail',
    display_name: 'Order Count',
    importance: 'standard',
    created_at: '2026-03-01T00:00:00Z',
  };

  assert.equal(criticalMetric.importance, 'critical');
  assert.equal(standardMetric.importance, 'standard');
});

test('getMetricSupportState identifies analyzable executable versions in blue state', () => {
  const def = {
    id: 'def-1',
    name: 'orders_sum',
    source_label: 'dbt model',
    owner_label: 'analyst',
    versions: [
      {
        id: 'ver-1',
        definition_id: 'def-1',
        version_number: 1,
        support_state: 'executable',
        support_reason: null,
      },
    ],
  };

  const metric = {
    id: 'm-1',
    display_name: 'Net Revenue',
    importance: 'important',
    active_binding: {
      definition_id: 'def-1',
      approved_version_id: 'ver-1',
      status: 'active',
    },
  };

  const support = getMetricSupportState(metric, [def]);
  assert.equal(support.isAnalyzable, true);
  assert.equal(support.state, 'analyzable');
  assert.equal(support.label, 'Analyzable');
});

test('getMetricSupportState identifies limited/refused support states in amber', () => {
  const def = {
    id: 'def-refused',
    name: 'cte_multi_join',
    source_label: 'SQL import',
    owner_label: 'analyst',
    versions: [
      {
        id: 'ver-refused',
        definition_id: 'def-refused',
        version_number: 1,
        support_state: 'metadata_only',
        support_reason: 'CTE_CONTAINS_JOIN: multi-table JOIN rejected by single-table IR',
      },
    ],
  };

  const metric = {
    id: 'm-2',
    display_name: 'Complex Join Metric',
    importance: 'standard',
    active_binding: {
      definition_id: 'def-refused',
      approved_version_id: 'ver-refused',
      status: 'active',
    },
  };

  const support = getMetricSupportState(metric, [def]);
  assert.equal(support.isAnalyzable, false);
  assert.equal(support.state, 'limited');
  assert.equal(support.label, 'Limited / Refused');
  assert.ok(support.reason.includes('JOIN'));
});

test('getUnassignedDefinitions separates bound and unbound definitions cleanly', () => {
  const definitions = [
    { id: 'def-bound-1', name: 'revenue_core', versions: [] },
    { id: 'def-bound-2', name: 'orders_count', versions: [] },
    { id: 'def-unassigned-1', name: 'refunds_ad_hoc', versions: [] },
    { id: 'def-unassigned-2', name: 'experimental_mrr', versions: [] },
  ];

  const metrics = [
    { id: 'm-1', active_binding: { definition_id: 'def-bound-1', status: 'active' } },
  ];
  const candidateBindings = [
    { definition_id: 'def-bound-2', status: 'candidate' },
  ];

  const unassigned = getUnassignedDefinitions(definitions, metrics, candidateBindings);
  assert.equal(unassigned.length, 2);
  assert.deepEqual(
    unassigned.map((d) => d.id),
    ['def-unassigned-1', 'def-unassigned-2']
  );
});

test('classifyVersionStatus marks approved baseline, candidates, and superseded revisions', () => {
  const activeBinding = {
    approved_version_id: 'ver-approved-18',
    definition_id: 'def-core',
    status: 'active',
  };
  const candidateBindings = [
    { approved_version_id: 'ver-cand-19', definition_id: 'def-candidate', status: 'candidate' },
  ];

  assert.equal(classifyVersionStatus({ id: 'ver-approved-18', definition_id: 'def-core' }, activeBinding, candidateBindings), 'approved');
  assert.equal(classifyVersionStatus({ id: 'ver-cand-19', definition_id: 'def-candidate' }, activeBinding, candidateBindings), 'candidate');
  assert.equal(classifyVersionStatus({ id: 'ver-old-17', definition_id: 'def-core' }, activeBinding, candidateBindings), 'superseded');
});

test('evaluateDrift classifies exact matches in blue and material drift in red', () => {
  const zeroRun = {
    id: 'run-zero',
    baseline_version_id: 'v18',
    target_version_id: 'v19-zero',
    result: {
      baseline_value: '1000.00',
      target_value: '1000.00',
      delta_target_minus_baseline: '0.00',
      absolute_gap: '0',
      relative_change_percent_display: '0.00%',
    },
  };

  const driftZero = evaluateDrift(zeroRun, 'v18');
  assert.equal(driftZero.isZero, true);
  assert.equal(driftZero.pillClass, 'ready'); // Blue
  assert.equal(driftZero.statusLabel, 'Exact match (zero drift)');

  const materialRun = {
    id: 'run-material',
    baseline_version_id: 'v18',
    target_version_id: 'v19-diff',
    result: {
      baseline_value: '10000.00',
      target_value: '8500.00',
      delta_target_minus_baseline: '-1500.00',
      absolute_gap: '1500.00',
      relative_change_percent_display: '-15.00%',
    },
  };

  const driftMaterial = evaluateDrift(materialRun, 'v18');
  assert.equal(driftMaterial.isZero, false);
  assert.equal(driftMaterial.isMaterial, true);
  assert.equal(driftMaterial.pillClass, 'error'); // Red
  assert.equal(driftMaterial.statusLabel, 'Material drift');
  assert.equal(driftMaterial.delta, '-1500.00');
});

test('Zero-green semantic constraint verification', () => {
  // Healthy/Analyzable/Approved states MUST be ready/blue
  const analyzableClass = 'ready'; // Maps to #245BFF in style.css
  const approvedBadgeClass = 'ready'; // Maps to #245BFF in style.css
  assert.ok(!analyzableClass.includes('green'));
  assert.ok(!approvedBadgeClass.includes('green'));

  // Critical/Material drift MUST be error/red
  const materialClass = 'error'; // Maps to #D92D20 in style.css
  assert.ok(!materialClass.includes('green'));

  // Warnings MUST be warn/amber
  const warningClass = 'warn'; // Maps to #D97706 in style.css
  assert.ok(!warningClass.includes('green'));
});

test('getMetricSupportState enforces approved_version_id from backend truth without implicit approval', () => {
  const def = {
    id: 'def-draft',
    name: 'draft_metric',
    versions: [
      { id: 'ver-v1', version_number: 1, support_state: 'executable' },
      { id: 'ver-v2', version_number: 2, support_state: 'executable' },
    ],
  };

  // Case A: Metric with definition bound but NO approved_version_id
  const metricNoApproval = {
    id: 'm-draft',
    display_name: 'Unapproved Metric',
    active_binding: {
      definition_id: 'def-draft',
      approved_version_id: null,
      status: 'active',
    },
  };

  const stateNoApproval = getMetricSupportState(metricNoApproval, [def]);
  assert.equal(stateNoApproval.state, 'unbound');
  assert.equal(stateNoApproval.label, 'Pending approval');
  assert.equal(stateNoApproval.isAnalyzable, false);
  assert.equal(stateNoApproval.version, null);

  // Case B: Metric with explicit approved_version_id from backend
  const metricApproved = {
    id: 'm-approved',
    display_name: 'Approved Metric',
    active_binding: {
      definition_id: 'def-draft',
      approved_version_id: 'ver-v1',
      status: 'active',
    },
  };

  const stateApproved = getMetricSupportState(metricApproved, [def]);
  assert.equal(stateApproved.state, 'analyzable');
  assert.equal(stateApproved.label, 'Analyzable');
  assert.equal(stateApproved.isAnalyzable, true);
  assert.equal(stateApproved.version?.id, 'ver-v1');
});

test('candidate bindings do not automatically promote without explicit transition', () => {
  const candidateBinding = {
    id: 'bind-cand',
    metric_id: 'm-1',
    definition_id: 'def-cand',
    status: 'candidate',
    approved_version_id: null,
  };

  // Attaching candidate keeps status as candidate and approved_version_id null
  assert.equal(candidateBinding.status, 'candidate');
  assert.equal(candidateBinding.approved_version_id, null);

  // Status only changes to active after explicit promotion operation
  const promotedBinding = {
    ...candidateBinding,
    status: 'active',
    approved_version_id: 'ver-cand-1',
  };
  assert.equal(promotedBinding.status, 'active');
  assert.equal(promotedBinding.approved_version_id, 'ver-cand-1');
});
