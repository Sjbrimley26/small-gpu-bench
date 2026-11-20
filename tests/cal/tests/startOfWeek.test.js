import { test } from 'node:test';
import assert from 'node:assert/strict';
import { startOfWeek } from '../app/src/gen/startOfWeek.js';

const ymd = (d) => [d.getFullYear(), d.getMonth() + 1, d.getDate()];

test('Sunday start: a Wednesday goes back to the previous Sunday', () => {
  const r = startOfWeek(new Date(2026, 9, 21, 15, 30), 0); // Wed Oct 21 2026
  assert.deepEqual(ymd(r), [2026, 10, 18]);
});

test('Monday start: a Sunday goes back six days', () => {
  const r = startOfWeek(new Date(2026, 9, 18), 1); // Sun Oct 18 2026
  assert.deepEqual(ymd(r), [2026, 10, 12]);
});

test('weekStartsOn defaults to 0 (Sunday)', () => {
  const r = startOfWeek(new Date(2026, 9, 20));
  assert.deepEqual(ymd(r), [2026, 10, 18]);
});

test('a date that is already the week start returns the same day', () => {
  assert.deepEqual(ymd(startOfWeek(new Date(2026, 9, 18, 23, 59), 0)), [2026, 10, 18]);
  assert.deepEqual(ymd(startOfWeek(new Date(2026, 9, 19, 8), 1)), [2026, 10, 19]);
});

test('crosses month and year boundaries', () => {
  const r = startOfWeek(new Date(2026, 0, 1), 0); // Thu Jan 1 2026
  assert.deepEqual(ymd(r), [2025, 12, 28]);
});

test('result is at 00:00:00.000 local time', () => {
  const r = startOfWeek(new Date(2026, 9, 21, 15, 30, 12, 345), 0);
  assert.equal(r.getHours(), 0);
  assert.equal(r.getMinutes(), 0);
  assert.equal(r.getSeconds(), 0);
  assert.equal(r.getMilliseconds(), 0);
});

test('returns a new Date and does not mutate the input', () => {
  const input = new Date(2026, 9, 21, 15, 30);
  const before = input.getTime();
  const r = startOfWeek(input, 0);
  assert.ok(r instanceof Date);
  assert.notEqual(r, input);
  assert.equal(input.getTime(), before);
});
