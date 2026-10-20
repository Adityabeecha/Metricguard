import assert from 'node:assert/strict';
import { test } from 'node:test';
import { authPage, oauthErrorMessage, configuredProviders, safeWorkspaceReturn, shouldRedirectToSignIn } from './authFlow.ts';

test('sign-in and sign-up are public auth routes with optional trailing slash', () => {
  assert.equal(authPage('/signin'), 'signin');
  assert.equal(authPage('/signup/'), 'signup');
  assert.equal(authPage('/w/acme/settings'), null);
  assert.equal(authPage('/signin/unknown'), null);
});

test('OAuth errors use friendly fixed copy and never echo untrusted callback text', () => {
  assert.equal(oauthErrorMessage('waitlist'), "You're on the waitlist — we'll email you when your invite is ready");
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
