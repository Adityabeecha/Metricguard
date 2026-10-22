import assert from 'node:assert/strict';
import { test } from 'node:test';
import { authPage, authPageTitle, accountInitials, accountMenuActions, endAccountSession, localKeyAccess, signOutDestination, connectionControls, oauthErrorMessage, configuredProviders, safeWorkspaceReturn, shouldRedirectToSignIn } from './authFlow.ts';

test('account initials use the signed-in name, with email and unknown fallbacks', () => {
  assert.equal(accountInitials({ name: '  Ada   Lovelace ', email: 'ada@example.com' }), 'AL');
  assert.equal(accountInitials({ name: 'Prince' }), 'P');
  assert.equal(accountInitials({ name: 'Élodie Van Dijk' }), 'ÉD');
  assert.equal(accountInitials({ name: ' ', email: 'grace@example.com' }), 'G');
  assert.equal(accountInitials(null), '?');
});

test('account actions preserve settings and sign-out while keys require confirmed local access', () => {
  const cookie = { verified: true, session: true };
  assert.deepEqual(accountMenuActions(cookie, true), ['settings', 'signout']);
  assert.deepEqual(accountMenuActions(cookie, false), ['settings', 'signout']);
  assert.deepEqual(accountMenuActions({ verified: true, session: false }, true), ['settings', 'signout']);
  assert.deepEqual(accountMenuActions({ verified: true, session: false }, false), ['settings', 'key', 'signout']);
  assert.deepEqual(accountMenuActions({ verified: false }, false), ['settings', 'key']);
  assert.deepEqual(accountMenuActions(null, false, true), ['settings', 'signout']);
  assert.deepEqual(accountMenuActions(null, false, true, true), ['settings', 'key', 'signout']);
  assert.deepEqual(accountMenuActions(null, false), ['settings']);
  for (const unknown of [null, undefined]) assert.deepEqual(accountMenuActions(null, unknown), ['settings']);
  assert.equal(localKeyAccess(false), true);
  assert.equal(localKeyAccess(true), false);
  assert.equal(localKeyAccess(undefined), false);
});

test('successful sign-out clears identity only after the server has ended the session', async () => {
  const events = [];
  let signedIn = true;
  await endAccountSession(async () => { events.push('server'); }, () => { events.push('clear'); signedIn = false; });
  assert.equal(signedIn, false);
  assert.deepEqual(events, ['server', 'clear']);
  assert.equal(signOutDestination(true), '/signin');
  assert.equal(signOutDestination(undefined), '/signin');
  assert.equal(signOutDestination(false), null);
});

test('failed server sign-out retains the current identity so the user can retry', async () => {
  let signedIn = true;
  await assert.rejects(endAccountSession(async () => { throw new Error('offline'); }, () => { signedIn = false; }), /offline/);
  assert.equal(signedIn, true);
});

test('sign-in and sign-up are public auth routes with optional trailing slash', () => {
  assert.equal(authPage('/signin'), 'signin');
  assert.equal(authPage('/signup/'), 'signup');
  assert.equal(authPage('/w/acme/settings'), null);
  assert.equal(authPage('/signin/unknown'), null);
});

test('auth routes have distinct browser titles for direct visits and mode switches', () => {
  for (const [path, title] of [
    ['/signin', 'Sign in — MetricGuard'],
    ['/signup/', 'Create your account — MetricGuard'],
    ['/signin/', 'Sign in — MetricGuard'],
  ]) {
    assert.equal(authPageTitle(authPage(path)), title);
  }
});

test('cookie identities never offer API-key disconnect or replacement actions', () => {
  assert.deepEqual(connectionControls({ session: true }, true, false), { disconnect: false, connect: false });
  // The cookie may be verified before the shared signed-in flag is restored.
  assert.deepEqual(connectionControls({ session: true }, false, false), { disconnect: false, connect: false });
  // A workspace access failure must not turn cookie sign-in into key management.
  assert.deepEqual(connectionControls({ session: true }, true, true), { disconnect: false, connect: false });
});

test('API keys and local access retain key controls, including invalid-key recovery', () => {
  assert.deepEqual(connectionControls({ session: false }, true, false), { disconnect: true, connect: true });
  assert.deepEqual(connectionControls({ session: false }, false, false), { disconnect: false, connect: true });
  assert.deepEqual(connectionControls(null, true, true), { disconnect: true, connect: true });
  assert.deepEqual(connectionControls(null, false, true), { disconnect: false, connect: true });
});

test('key actions wait for identity verification to avoid flashing on cookie-session reloads', () => {
  assert.deepEqual(connectionControls(null, true, false), { disconnect: false, connect: false });
  assert.deepEqual(connectionControls(null, false, false), { disconnect: false, connect: false });
});

test('OAuth errors use friendly fixed copy and never echo untrusted callback text', () => {
  assert.equal(oauthErrorMessage('waitlist'), 'We couldn’t sign you in. Please try again.'); // no waitlist: sign-up is open
  assert.match(oauthErrorMessage('cancelled'), /cancelled/i);
  assert.match(oauthErrorMessage('failed'), /try again/i);
  assert.equal(oauthErrorMessage('raw provider secret'), oauthErrorMessage('failed'));
  assert.equal(oauthErrorMessage(null), '');
});

test('only known configured providers are offered, once each', () => {
  assert.deepEqual(configuredProviders({ providers: ['github', 'unknown', 'google', 'github'] }), ['google', 'github']);
  assert.deepEqual(configuredProviders({ providers: 'google' }), []);
  assert.deepEqual(configuredProviders(null), []);
});

test('return destinations remain within a workspace on this origin', () => {
  assert.equal(safeWorkspaceReturn('/w/acme/changes?tab=history'), '/w/acme/changes?tab=history');
  for (const path of ['https://evil.test', '//evil.test/w/acme', '/signin', '/w/acme/../../evil', '/w/acme/\\evil', '/w/acme/%0aevil']) {
    assert.equal(safeWorkspaceReturn(path), null, path);
  }
});

test('hosted signed-out gates redirect while local access and invalid keys retain existing flows', () => {
  const required = { kind: 'signin', reason: 'required' };
  assert.equal(shouldRedirectToSignIn(required, true, false), true);
  assert.equal(shouldRedirectToSignIn(required, false, false), false);
  assert.equal(shouldRedirectToSignIn(required, true, true), false);
  assert.equal(shouldRedirectToSignIn({ kind: 'denied' }, true, false), false);
});

test('guest notice counts down and is empty for full accounts', async () => {
  const { guestNotice } = await import('./authFlow.ts');
  const now = 1_000_000_000_000;
  assert.equal(guestNotice(null, now), '');
  assert.equal(guestNotice(undefined, now), '');
  assert.match(guestNotice(now / 1000 + 8 * 3600, now), /erased in 8 h\. Storage is limited to 25 MB/);
  assert.match(guestNotice(now / 1000 + 95 * 60, now), /in 1 h 35 min/);
  assert.match(guestNotice(now / 1000 + 20, now), /any minute now/);
});
