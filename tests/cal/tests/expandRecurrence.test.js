import { test } from 'node:test';
import assert from 'node:assert/strict';
import { expandRecurrence } from '../app/src/gen/expandRecurrence.js';

// Run in a DST-observing zone so implementations that use local-time Date maths get caught.
process.env.TZ = 'America/New_York';

const deepFreeze = (o) => {
  Object.values(o).forEach((v) => typeof v === 'object' && v && deepFreeze(v));
  return Object.freeze(o);
};
const run = (start, rule, from, to, exdates) =>
  expandRecurrence(deepFreeze({ start, rule, ...(exdates ? { exdates } : {}) }), from, to);
const at = (time, ...dates) => dates.map((d) => `${d}T${time}`);

test('daily: basic window is [from, to)', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily' }, '2026-01-01', '2026-01-04'),
    at('09:00', '2026-01-01', '2026-01-02', '2026-01-03'));
});
test('daily: interval 3', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily', interval: 3 }, '2026-01-01', '2026-01-14'),
    at('09:00', '2026-01-01', '2026-01-04', '2026-01-07', '2026-01-10', '2026-01-13'));
});
test('daily: window starts after series start', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily' }, '2026-01-10', '2026-01-12'),
    at('09:00', '2026-01-10', '2026-01-11'));
});
test('window entirely before the start gives []', () => {
  assert.deepEqual(run('2026-03-01T09:00', { freq: 'daily' }, '2026-01-01', '2026-02-01'), []);
});
test('empty window gives []', () => {
  assert.deepEqual(run('2026-03-01T09:00', { freq: 'daily' }, '2026-03-05', '2026-03-05'), []);
});
test('daily: crosses year boundary', () => {
  assert.deepEqual(run('2026-12-30T08:15', { freq: 'daily' }, '2026-12-30', '2027-01-03'),
    at('08:15', '2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02'));
});
test('daily: leap day exists in 2028 but not 2027', () => {
  assert.deepEqual(run('2028-02-27T09:00', { freq: 'daily' }, '2028-02-27', '2028-03-02'),
    at('09:00', '2028-02-27', '2028-02-28', '2028-02-29', '2028-03-01'));
  assert.deepEqual(run('2027-02-27T09:00', { freq: 'daily' }, '2027-02-27', '2027-03-02'),
    at('09:00', '2027-02-27', '2027-02-28', '2027-03-01'));
});
test('weekly without byDay repeats the start weekday', () => {
  assert.deepEqual(run('2026-03-04T18:30', { freq: 'weekly' }, '2026-03-01', '2026-03-30'),
    at('18:30', '2026-03-04', '2026-03-11', '2026-03-18', '2026-03-25'));
});
test('weekly byDay: days before the start date in the first week are not generated', () => {
  assert.deepEqual(run('2026-03-04T10:00', { freq: 'weekly', byDay: ['MO', 'WE', 'FR'] }, '2026-03-01', '2026-03-14'),
    at('10:00', '2026-03-04', '2026-03-06', '2026-03-09', '2026-03-11', '2026-03-13'));
});
test('weekly interval 2 with byDay', () => {
  assert.deepEqual(run('2026-03-03T08:00', { freq: 'weekly', interval: 2, byDay: ['TU', 'TH'] }, '2026-03-01', '2026-04-04'),
    at('08:00', '2026-03-03', '2026-03-05', '2026-03-17', '2026-03-19', '2026-03-31', '2026-04-02'));
});
test('weekly byDay: unsorted and duplicate codes still give sorted, unique output', () => {
  assert.deepEqual(run('2026-03-02T09:00', { freq: 'weekly', byDay: ['FR', 'MO', 'MO'] }, '2026-03-01', '2026-03-15'),
    at('09:00', '2026-03-02', '2026-03-06', '2026-03-09', '2026-03-13'));
});
test('weekly: weeks start on Monday (Sunday is the LAST day of its week)', () => {
  assert.deepEqual(run('2026-03-07T09:00', { freq: 'weekly', interval: 2, byDay: ['SA', 'SU'] }, '2026-03-01', '2026-03-30'),
    at('09:00', '2026-03-07', '2026-03-08', '2026-03-21', '2026-03-22'));
});
test('monthly on the 31st skips months without a 31st', () => {
  assert.deepEqual(run('2026-01-31T12:00', { freq: 'monthly' }, '2026-01-01', '2026-09-01'),
    at('12:00', '2026-01-31', '2026-03-31', '2026-05-31', '2026-07-31', '2026-08-31'));
});
test('monthly on the 29th skips February in a non-leap year only', () => {
  assert.deepEqual(run('2027-01-29T09:00', { freq: 'monthly' }, '2027-01-01', '2027-04-01'),
    at('09:00', '2027-01-29', '2027-03-29'));
  assert.deepEqual(run('2028-01-29T09:00', { freq: 'monthly' }, '2028-01-01', '2028-04-01'),
    at('09:00', '2028-01-29', '2028-02-29', '2028-03-29'));
});
test('monthly interval 2', () => {
  assert.deepEqual(run('2026-01-15T09:00', { freq: 'monthly', interval: 2 }, '2026-01-01', '2026-08-01'),
    at('09:00', '2026-01-15', '2026-03-15', '2026-05-15', '2026-07-15'));
});
test('monthly: rolls over the year', () => {
  assert.deepEqual(run('2026-11-15T10:00', { freq: 'monthly' }, '2026-11-01', '2027-03-01'),
    at('10:00', '2026-11-15', '2026-12-15', '2027-01-15', '2027-02-15'));
});
test('count limits the series', () => {
  assert.deepEqual(run('2026-03-04T18:30', { freq: 'weekly', count: 3 }, '2026-01-01', '2027-01-01'),
    at('18:30', '2026-03-04', '2026-03-11', '2026-03-18'));
});
test('count is counted from the series start, not from the window', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily', count: 5 }, '2026-01-04', '2026-02-01'),
    at('09:00', '2026-01-04', '2026-01-05'));
});
test('exdates are removed but still count toward count', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily', count: 5 }, '2026-01-01', '2026-02-01', ['2026-01-03']),
    at('09:00', '2026-01-01', '2026-01-02', '2026-01-04', '2026-01-05'));
});
test('monthly count does not count skipped months', () => {
  assert.deepEqual(run('2026-01-31T12:00', { freq: 'monthly', count: 3 }, '2026-01-01', '2027-01-01'),
    at('12:00', '2026-01-31', '2026-03-31', '2026-05-31'));
});
test('until is inclusive', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily', until: '2026-01-03' }, '2026-01-01', '2026-02-01'),
    at('09:00', '2026-01-01', '2026-01-02', '2026-01-03'));
});
test('count and until: whichever ends the series first wins', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily', count: 2, until: '2026-01-10' }, '2026-01-01', '2026-02-01'),
    at('09:00', '2026-01-01', '2026-01-02'));
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily', count: 10, until: '2026-01-03' }, '2026-01-01', '2026-02-01'),
    at('09:00', '2026-01-01', '2026-01-02', '2026-01-03'));
});
test('window after until gives []', () => {
  assert.deepEqual(run('2026-01-01T09:00', { freq: 'daily', until: '2026-01-03' }, '2026-02-01', '2026-03-01'), []);
});
test('no rule: a single occurrence if inside the window', () => {
  assert.deepEqual(expandRecurrence({ start: '2026-03-04T10:00' }, '2026-03-01', '2026-03-10'), ['2026-03-04T10:00']);
  assert.deepEqual(expandRecurrence({ start: '2026-03-04T10:00' }, '2026-03-05', '2026-03-10'), []);
});
test('DST spring-forward: a nonexistent local time (02:30) is kept as wall-clock', () => {
  assert.deepEqual(run('2026-03-06T02:30', { freq: 'daily' }, '2026-03-06', '2026-03-11'),
    at('02:30', '2026-03-06', '2026-03-07', '2026-03-08', '2026-03-09', '2026-03-10'));
});
test('DST fall-back: an ambiguous local time (01:30) yields exactly one occurrence per day', () => {
  assert.deepEqual(run('2026-10-30T01:30', { freq: 'daily' }, '2026-10-30', '2026-11-04'),
    at('01:30', '2026-10-30', '2026-10-31', '2026-11-01', '2026-11-02', '2026-11-03'));
});
test('DST: weekly and monthly series across the spring-forward date', () => {
  assert.deepEqual(run('2026-03-01T02:30', { freq: 'weekly' }, '2026-03-01', '2026-03-23'),
    at('02:30', '2026-03-01', '2026-03-08', '2026-03-15', '2026-03-22'));
  assert.deepEqual(run('2026-02-08T02:30', { freq: 'monthly' }, '2026-02-01', '2026-05-01'),
    at('02:30', '2026-02-08', '2026-03-08', '2026-04-08'));
});
test('stress: yearly-style monthly rule from a leap day', () => {
  assert.deepEqual(run('2028-02-29T09:00', { freq: 'monthly', interval: 12, count: 3 }, '2028-01-01', '2040-01-01'),
    at('09:00', '2028-02-29', '2032-02-29', '2036-02-29'));
});
test('does not mutate its input (frozen event) and returns a fresh array', () => {
  const ev = deepFreeze({ start: '2026-01-01T09:00', rule: { freq: 'daily', count: 2 }, exdates: [] });
  const a = expandRecurrence(ev, '2026-01-01', '2026-02-01');
  const b = expandRecurrence(ev, '2026-01-01', '2026-02-01');
  assert.deepEqual(a, b);
  assert.notEqual(a, b);
});
