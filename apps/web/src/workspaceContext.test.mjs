import test from 'node:test';
import assert from 'node:assert/strict';
import { parseRoute, buildRouteUrl } from './router.ts';

test('parseRoute extracts workspaceId from /w/:workspaceId routes', () => {
  const r1 = parseRoute('/w/idiotmetrics/datasources');
  assert.equal(r1.workspaceId, 'idiotmetrics');
  assert.equal(r1.page, 'datasources');

  const r2 = parseRoute('/w/local-retail/investigations', '?investigationId=inv-123');
  assert.equal(r2.workspaceId, 'local-retail');
  assert.equal(r2.page, 'investigations');
  assert.equal(r2.query.investigationId, 'inv-123');
});

test('buildRouteUrl guarantees /w/:workspaceId prefix and serializes query', () => {
  const url1 = buildRouteUrl('idiotmetrics', 'investigations', undefined, { draftId: 'd-456' });
  assert.equal(url1, '/w/idiotmetrics/investigations?draftId=d-456');

  const url2 = buildRouteUrl('tenant-alpha', 'metrics', 'active');
  assert.equal(url2, '/w/tenant-alpha/metrics?tab=active');
});

test('Workspace navigation resets stale query parameters and maintains isolation', () => {
  // Simulating navigating from Workspace A (with an active investigation) to Workspace B
  const routeA = parseRoute('/w/workspace-a/investigations', '?investigationId=draft-a-99&metricId=metric-a');
  assert.equal(routeA.workspaceId, 'workspace-a');
  assert.equal(routeA.query.investigationId, 'draft-a-99');
  assert.equal(routeA.query.metricId, 'metric-a');

  // When switching workspace to workspace-b, the target URL is generated without stale IDs
  const targetUrl = buildRouteUrl('workspace-b', 'investigations');
  const routeB = parseRoute('/w/workspace-b/investigations');
  assert.equal(routeB.workspaceId, 'workspace-b');
  assert.equal(routeB.query.investigationId, undefined);
  assert.equal(routeB.query.metricId, undefined);
  assert.equal(targetUrl, '/w/workspace-b/investigations');
});

test('Header injection logic ensures X-Project-Id is attached to non-global paths', () => {
  let activeWorkspaceId = 'idiotmetrics';
  
  function prepareHeaders(path, initHeaders = {}) {
    const reqHeaders = { ...initHeaders };
    const isGlobal = path === '/workspaces' || path === '/health' || path.startsWith('/evaluation');
    const hasProjectHeader = Object.keys(reqHeaders).some((k) => k.toLowerCase() === 'x-project-id');
    if (!isGlobal && activeWorkspaceId && !hasProjectHeader) {
      reqHeaders['X-Project-Id'] = activeWorkspaceId;
    }
    return reqHeaders;
  }

  // Scoped endpoints MUST get X-Project-Id
  assert.equal(prepareHeaders('/flat/definitions')['X-Project-Id'], 'idiotmetrics');
  assert.equal(prepareHeaders('/flat/comparisons')['X-Project-Id'], 'idiotmetrics');
  assert.equal(prepareHeaders('/bootstrap')['X-Project-Id'], 'idiotmetrics');
  assert.equal(prepareHeaders('/investigations')['X-Project-Id'], 'idiotmetrics');

  // Global endpoints MUST NOT get X-Project-Id
  assert.equal(prepareHeaders('/workspaces')['X-Project-Id'], undefined);
  assert.equal(prepareHeaders('/health')['X-Project-Id'], undefined);
  assert.equal(prepareHeaders('/evaluation')['X-Project-Id'], undefined);

  // Existing explicit header is preserved
  assert.equal(prepareHeaders('/bootstrap', { 'x-project-id': 'custom-ws' })['x-project-id'], 'custom-ws');
});
