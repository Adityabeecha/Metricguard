import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseRoute, buildRouteUrl } from './router.ts';
import * as routing from './router.ts';

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

test('unknown pages are flagged instead of silently showing Cases', () => {
  const unknown = parseRoute('/w/local-retail/no-such-page');
  assert.equal(unknown.page, 'dashboard');
  assert.equal(unknown.notFound, true);
  assert.equal(parseRoute('/w/local-retail/data-sources').page, 'data');
  assert.equal(parseRoute('/w/local-retail/data-sources').notFound, undefined);
  assert.equal(parseRoute('/w/local-retail').notFound, undefined);
  assert.equal(parseRoute('/').notFound, undefined);
  assert.equal(parseRoute('/nonsense').notFound, true);
});

test('public routes stay outside workspace bootstrapping', () => {
  assert.equal(typeof routing.sitePage, 'function');
  for (const [path, page] of [['/', 'home'], ['/privacy', 'privacy'], ['/terms/', 'terms'],
    ['/signin', 'signin'], ['/signup', 'signup'], ['/w/acme/investigations', 'workspace']]) {
    assert.equal(routing.sitePage(path), page);
  }
});

test('homepage sends only verified identities with an accessible workspace to Cases', () => {
  assert.equal(typeof routing.homeRedirect, 'function');
  assert.equal(routing.homeRedirect(null), null);
  assert.equal(routing.homeRedirect({ verified: false, home_workspace_id: 'local-retail' }), null);
  assert.equal(routing.homeRedirect({ verified: true, home_workspace_id: 'personal-123' }), '/w/personal-123/investigations');
  assert.equal(routing.homeRedirect({ verified: true, home_workspace_id: 'team-analytics' }), '/w/team-analytics/investigations');
  for (const id of [null, '', '//evil.test', '../private', 'acme/../../evil']) {
    assert.equal(routing.homeRedirect({ verified: true, home_workspace_id: id }), null);
  }
});

test('unknown and partly matching paths resolve to the public 404', () => {
  assert.equal(parseRoute('/w/acme/investigations/case/extra').notFound, true);
  assert.equal(typeof routing.sitePage, 'function');
  for (const path of ['/no-such-page', '/privacy/extra', '/w/acme/no-such-page',
    '/w/acme/settings/extra', '/w/acme/investigations/case/extra', '/overview/extra']) {
    assert.equal(routing.sitePage(path), 'not-found', path);
  }
});
