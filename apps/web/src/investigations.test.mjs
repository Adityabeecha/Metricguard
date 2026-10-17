import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  buildComparisonPayload,
  classifyResidual,
  computeSqlDifferences,
  detectExecutionPath,
  formatInvestigationStatus,
  getSnapshotContractMetadata,
  humanizeMembership,
} from './investigationUtils.ts';

test('humanizeMembership maps internal inclusion states to plain language labels', () => {
  // Direct string membership inputs
  assert.equal(humanizeMembership('included_in_both'), 'Both');
  assert.equal(humanizeMembership('baseline_only'), 'Baseline only');
  assert.equal(humanizeMembership('target_only'), 'Target only');
  assert.equal(humanizeMembership('excluded_from_both'), 'Neither');

  // Boolean inclusion combinations
  assert.equal(humanizeMembership(null, true, true), 'Both');
  assert.equal(humanizeMembership(null, true, false), 'Baseline only');
  assert.equal(humanizeMembership(null, false, true), 'Target only');
  assert.equal(humanizeMembership(null, false, false), 'Neither');
});

test('computeSqlDifferences extracts added and removed filter constraints cleanly', () => {
  const baseSql = `SELECT SUM(amount) FROM snapshot WHERE status = 'completed'`;
  const targetSql = `SELECT SUM(amount) FROM snapshot WHERE status = 'completed' AND region = 'South'`;

  const diff = computeSqlDifferences(baseSql, targetSql);
  assert.equal(diff.hasDifferences, true);
  assert.equal(diff.addedFilters.length, 1);
  assert.equal(diff.addedFilters[0], "region = 'South'");
  assert.equal(diff.removedFilters.length, 0);
  assert.match(diff.summary, /Target adds filter constraint/);

  // Identical SQL
  const identicalDiff = computeSqlDifferences(baseSql, baseSql);
  assert.equal(identicalDiff.hasDifferences, false);
  assert.equal(identicalDiff.addedFilters.length, 0);
  assert.equal(identicalDiff.removedFilters.length, 0);

  // Removed filter
  const removedDiff = computeSqlDifferences(targetSql, baseSql);
  assert.equal(removedDiff.hasDifferences, true);
  assert.equal(removedDiff.removedFilters.length, 1);
  assert.equal(removedDiff.removedFilters[0], "region = 'South'");
});

test('classifyResidual handles zero and non-zero residual variances without false alarms', () => {
  assert.equal(classifyResidual(null), 'Residual not applicable');
  assert.equal(classifyResidual(''), 'Residual not applicable');
  assert.equal(classifyResidual(undefined), 'Residual not applicable');
  assert.equal(classifyResidual('0.00'), 'No unexplained residual');
  assert.equal(classifyResidual('0'), 'No unexplained residual');
  assert.equal(classifyResidual('12.50', 'INR'), '₹12.50');
  assert.equal(classifyResidual('-5.00', 'USD'), 'USD -5.00');
});

test('formatInvestigationStatus produces correct badges and strictly respects zero-green', () => {
  const draftStatus = formatInvestigationStatus('draft');
  assert.equal(draftStatus.label, 'Draft');
  assert.equal(draftStatus.tone, 'blue');

  const runningStatus = formatInvestigationStatus('running');
  assert.equal(runningStatus.label, 'Running');
  assert.equal(runningStatus.tone, 'blue');

  const completedStatus = formatInvestigationStatus('completed');
  assert.equal(completedStatus.label, 'Completed');
  assert.equal(completedStatus.tone, 'blue');

  const needsDecisionStatus = formatInvestigationStatus('needs_decision');
  assert.equal(needsDecisionStatus.label, 'Needs decision');
  assert.equal(needsDecisionStatus.tone, 'amber');

  const governedCandidateStatus = formatInvestigationStatus('succeeded', true, false);
  assert.equal(governedCandidateStatus.label, 'Needs decision');
  assert.equal(governedCandidateStatus.tone, 'amber');

  // Strict check that NO status returns green
  const allTones = [draftStatus.tone, runningStatus.tone, completedStatus.tone, needsDecisionStatus.tone, governedCandidateStatus.tone];
  for (const tone of allTones) {
    assert.notEqual(tone, 'green');
    assert.notEqual(tone, 'success');
  }
});

test('detectExecutionPath selects flat vs standard without forcing user selection', () => {
  const flatVer = { execution_policy: 'flat_snapshot_v1' };
  const standardVer = { execution_policy: 'sql_ast_v1' };
  const flatSnap = { execution_policy: 'flat_snapshot_v1' };
  const standardSnap = { execution_policy: 'duckdb_table_v1' };

  assert.equal(detectExecutionPath(flatVer, standardVer, standardSnap), 'flat');
  assert.equal(detectExecutionPath(standardVer, flatVer, standardSnap), 'flat');
  assert.equal(detectExecutionPath(standardVer, standardVer, flatSnap), 'flat');
  assert.equal(detectExecutionPath(standardVer, standardVer, standardSnap), 'standard');
  assert.equal(detectExecutionPath(null, null, null), 'standard');
});

test('snapshot context regression: switching from sales INR snapshot to inventory count snapshot updates context immediately without carrying stale INR', () => {
  const salesSnapshot = {
    id: 'snap-sales-01',
    name: '01_sales_multi_region',
    currency: 'INR',
    execution_policy: 'flat_snapshot_v1',
    contract_hash: 'hash-sales-123',
    scope: 'entire_supplied_snapshot',
    manifest: {
      unit_label: 'INR',
      contract: {
        measure: 'revenue',
        unit_label: 'INR',
        scope: 'entire_supplied_snapshot',
      },
    },
  };

  const inventorySnapshot = {
    id: 'snap-inv-03',
    name: '03_inventory_units',
    currency: null,
    execution_policy: 'flat_snapshot_v1',
    contract_hash: 'f323ef0b248bd3b131c55f8dc22f3b33f3d3594cd64e497a820207dc4d1d2500',
    scope: 'entire_supplied_snapshot',
    unit_label: 'count',
    manifest: {
      unit_label: 'count',
      contract: {
        measure: 'units_on_hand',
        unit_label: 'count',
        scope: 'entire_supplied_snapshot',
      },
    },
  };

  const mockData = {
    flat_contracts: [
      {
        contract_hash: 'hash-sales-123',
        measure: 'revenue',
        unit_label: 'INR',
        scope: 'entire_supplied_snapshot',
        categories: ['region'],
      },
      {
        contract_hash: 'f323ef0b248bd3b131c55f8dc22f3b33f3d3594cd64e497a820207dc4d1d2500',
        measure: 'units_on_hand',
        unit_label: 'count',
        scope: 'entire_supplied_snapshot',
        categories: ['warehouse'],
      },
    ],
  };

  // Step 1: Select sales snapshot with unit INR
  const salesMeta = getSnapshotContractMetadata(salesSnapshot, mockData);
  assert.equal(salesMeta.unitLabel, 'INR');

  const salesPayload = buildComparisonPayload({
    isAdHocSql: true,
    baselineVersionId: 'ver-sales-base',
    targetVersionId: 'ver-sales-tgt',
    snapshotId: salesSnapshot.id,
    relationship: 'candidate',
    snapshot: salesSnapshot,
    data: mockData,
  });

  assert.equal(salesPayload.endpoint, '/flat/comparisons');
  assert.equal(salesPayload.body.context.unit_label, 'INR');

  // Step 2: Switch to inventory snapshot with unit count
  const invMeta = getSnapshotContractMetadata(inventorySnapshot, mockData);
  assert.equal(invMeta.unitLabel, 'count');
  assert.equal(invMeta.scope, 'entire_supplied_snapshot');
  assert.equal(invMeta.contractHash, 'f323ef0b248bd3b131c55f8dc22f3b33f3d3594cd64e497a820207dc4d1d2500');

  // Step 3: Run Comparison payload must contain unit_label=count
  const invPayload = buildComparisonPayload({
    isAdHocSql: true,
    baselineVersionId: 'ver-inv-base',
    targetVersionId: 'ver-inv-tgt',
    snapshotId: inventorySnapshot.id,
    relationship: 'candidate',
    snapshot: inventorySnapshot,
    data: mockData,
  });

  assert.equal(invPayload.endpoint, '/flat/comparisons');
  assert.equal(invPayload.body.context.unit_label, 'count');
  assert.equal(invPayload.body.context.scope, 'entire_supplied_snapshot');
  assert.equal(invPayload.body.context.contract_hash, 'f323ef0b248bd3b131c55f8dc22f3b33f3d3594cd64e497a820207dc4d1d2500');

  // Step 4: It must NOT contain INR
  const serialized = JSON.stringify(invPayload);
  assert.equal(serialized.includes('INR'), false, 'Inventory payload must strictly NOT contain INR');
});

