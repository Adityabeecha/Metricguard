import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createWakingTracker } from './wakingServer.ts';

test('slow requests show one note until the last slow request finishes', () => {
  const events = [], timers = new Map(); let id = 0;
  const track = createWakingTracker(value => events.push(value), (fn, delay) => { assert.equal(delay, 3000); timers.set(++id, fn); return id; }, n => timers.delete(n));
  const fast = track(); fast(); assert.equal(timers.size, 0);
  const first = track(), second = track();
  for (const fn of timers.values()) fn();
  assert.deepEqual(events, [true]);
  first(); assert.deepEqual(events, [true]);
  second(); assert.deepEqual(events, [true, false]);
});
