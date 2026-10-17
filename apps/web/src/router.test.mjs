import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseRoute, buildRouteUrl } from './router.ts';

test('parseRoute parses workspace routes correctly', () => {
  const r1 = parseRoute('/w/local-retail/dashboard');
  assert.equal(r1.workspaceId, 'local-retail');
  assert.equal(r1.page, 'dashboard');

  const r2 = parseRoute('/w/analytics-team/investigations');
  assert.equal(r2.workspaceId, 'analytics-team');
  assert.equal(r2.page, 'investigations');

  const r3 = parseRoute('/w/finance/metrics', '?tab=history');
  assert.equal(r3.workspaceId, 'finance');
  assert.equal(r3.page, 'metrics');
  const r4 = parseRoute('/w/local-retail/changes');
  assert.equal(r4.workspaceId, 'local-retail');
  assert.equal(r4.page, 'changes');

  const r5 = parseRoute('/w/local-retail/changes/diff-99abc');
  assert.equal(r5.workspaceId, 'local-retail');
  assert.equal(r5.page, 'changes');
  assert.equal(r5.query.changeId, 'diff-99abc');
});

test('parseRoute handles legacy routes with fallback to default workspace', () => {
  const r1 = parseRoute('/compare');
  assert.equal(r1.workspaceId, 'local-retail');
  assert.equal(r1.page, 'investigations');

  const r2 = parseRoute('/flat');
  assert.equal(r2.workspaceId, 'local-retail');
  assert.equal(r2.page, 'investigations');
  assert.equal(r2.query.mode, 'flat');

  const r3 = parseRoute('/evaluation');
  assert.equal(r3.workspaceId, 'local-retail');
  assert.equal(r3.page, 'settings');
  assert.equal(r3.tab, 'evaluation');

  const r4 = parseRoute('/');
  assert.equal(r4.workspaceId, 'local-retail');
  assert.equal(r4.page, 'dashboard');
});

test('buildRouteUrl constructs standard URL with workspace and parameters', () => {
  assert.equal(buildRouteUrl('acme', 'dashboard'), '/w/acme/dashboard');
  assert.equal(buildRouteUrl('acme', 'settings', 'integrations'), '/w/acme/settings?tab=integrations');
  assert.equal(buildRouteUrl('acme', 'investigations', undefined, { mode: 'flat' }), '/w/acme/investigations?mode=flat');
  assert.equal(buildRouteUrl('acme', 'changes'), '/w/acme/changes');
  assert.equal(buildRouteUrl('acme', 'changes', undefined, { changeId: 'diff-123' }), '/w/acme/changes/diff-123');
});
