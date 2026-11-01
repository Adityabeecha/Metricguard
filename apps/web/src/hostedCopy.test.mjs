import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isLocalHost, engineDownMessage } from './hostedCopy.ts';

test('local hosts receive local startup instructions', () => {
  for (const host of ['localhost', '127.0.0.1', '[::1]']) {
    assert.equal(isLocalHost(host), true);
    assert.match(engineDownMessage(host), /scripts\\mg\.ps1 start/);
  }
});

test('hosted users receive a retry and feedback message', () => {
  assert.equal(isLocalHost('metricguard.vercel.app'), false);
  assert.equal(engineDownMessage('metricguard.vercel.app'), 'The comparison engine is busy or restarting. Try again in a minute; if it keeps happening, send Feedback.');
});
