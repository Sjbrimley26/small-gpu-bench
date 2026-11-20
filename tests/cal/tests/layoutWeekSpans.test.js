import { test } from 'node:test';
import assert from 'node:assert/strict';
import { layoutWeekSpans } from '../app/src/gen/layoutWeekSpans.js';

// DST-observing zone: local-midnight maths gives a 23-hour day on 2026-03-08.
process.env.TZ = 'America/New_York';

// 2026-03-01 is a Sunday: col 0 = Mar 1 ... col 6 = Mar 7.
const WEEK = '2026-03-01';
const Z = [0, 0, 0, 0, 0, 0, 0];
const seg = (id, startCol, endCol, lane, continuesBefore = false, continuesAfter = false) =>
  ({ id, startCol, endCol, lane, continuesBefore, continuesAfter });
const ev = (id, start, end) => (end === undefined ? { id, start } : { id, start, end });
const run = (events, week = WEEK, maxLanes) =>
  layoutWeekSpans(Object.freeze(events.map((e) => Object.freeze(e))), week, ...(maxLanes === undefined ? [] : [maxLanes]));

test('no events', () => {
  assert.deepEqual(run([]), { segments: [], hidden: Z });
});
test('single-day event with no end', () => {
  assert.deepEqual(run([ev('a', '2026-03-04')]), { segments: [seg('a', 3, 3, 0)], hidden: Z });
});
test('multi-day event inside the week', () => {
  assert.deepEqual(run([ev('a', '2026-03-02', '2026-03-04')]).segments, [seg('a', 1, 3, 0)]);
});
test('starts before the week: clipped at column 0, continuesBefore', () => {
  assert.deepEqual(run([ev('a', '2026-02-27', '2026-03-03')]).segments, [seg('a', 0, 2, 0, true, false)]);
});
test('ends after the week: clipped at column 6, continuesAfter', () => {
  assert.deepEqual(run([ev('a', '2026-03-06', '2026-03-10')]).segments, [seg('a', 5, 6, 0, false, true)]);
});
test('spans the whole week and beyond on both sides', () => {
  assert.deepEqual(run([ev('a', '2026-02-20', '2026-03-20')]).segments, [seg('a', 0, 6, 0, true, true)]);
});
test('events outside the week are dropped; edge days are kept', () => {
  const out = run([
    ev('before', '2026-02-20', '2026-02-28'),
    ev('after', '2026-03-08', '2026-03-09'),
    ev('lastDayBefore', '2026-02-27', '2026-03-01'),
    ev('firstDayAfter', '2026-03-07', '2026-03-07'),
  ]).segments;
  assert.deepEqual(out, [seg('lastDayBefore', 0, 0, 0, true, false), seg('firstDayAfter', 6, 6, 0)]);
});
test('a week-ending event does not continue after; one ending on day 7 is exact', () => {
  assert.deepEqual(run([ev('a', '2026-03-05', '2026-03-07')]).segments, [seg('a', 4, 6, 0, false, false)]);
});
test('end before start is treated as a single day', () => {
  assert.deepEqual(run([ev('x', '2026-03-04', '2026-03-02')]).segments, [seg('x', 3, 3, 0)]);
});
test('lanes: non-overlapping events share a lane, overlapping ones stack', () => {
  const out = run([
    ev('A', '2026-03-02', '2026-03-04'), // cols 1-3
    ev('B', '2026-03-03', '2026-03-05'), // cols 2-4
    ev('C', '2026-03-05', '2026-03-06'), // cols 4-5
  ]).segments;
  assert.deepEqual(out, [seg('A', 1, 3, 0), seg('B', 2, 4, 1), seg('C', 4, 5, 0)]);
});
test('order: same start column, longer span first', () => {
  const out = run([ev('X', '2026-03-02'), ev('Y', '2026-03-02', '2026-03-05')]).segments;
  assert.deepEqual(out, [seg('Y', 1, 4, 0), seg('X', 1, 1, 1)]);
});
test('order: same start and span, id ascending', () => {
  const out = run([ev('b', '2026-03-03', '2026-03-04'), ev('a', '2026-03-03', '2026-03-04')]).segments;
  assert.deepEqual(out, [seg('a', 2, 3, 0), seg('b', 2, 3, 1)]);
});
test('order uses the span clipped to this week, not the original length', () => {
  const out = run([
    ev('P', '2026-02-25', '2026-03-02'), // original 6 days, clipped cols 0-1
    ev('Q', '2026-03-01', '2026-03-04'), // 4 days, cols 0-3
  ]).segments;
  assert.deepEqual(out, [seg('Q', 0, 3, 0), seg('P', 0, 1, 1, true, false)]);
});
test('a freed lane is reused', () => {
  const out = run([
    ev('E', '2026-03-01', '2026-03-07'), // 0-6
    ev('D', '2026-03-01'), // 0-0
    ev('F', '2026-03-02', '2026-03-03'), // 1-2
  ]).segments;
  assert.deepEqual(out, [seg('E', 0, 6, 0), seg('D', 0, 0, 1), seg('F', 1, 2, 1)]);
});
test('the LOWEST free lane is chosen', () => {
  const out = run([
    ev('H', '2026-03-01', '2026-03-02'), // 0-1 -> lane 0
    ev('G', '2026-03-01'), // 0-0 -> lane 1
    ev('I', '2026-03-03', '2026-03-04'), // 2-3 -> both lanes free; must pick 0
  ]).segments;
  assert.deepEqual(out, [seg('H', 0, 1, 0), seg('G', 0, 0, 1), seg('I', 2, 3, 0)]);
});
test('maxLanes: events beyond the limit are removed and counted per day', () => {
  const r = run([ev('a', '2026-03-03'), ev('b', '2026-03-03'), ev('c', '2026-03-03')], WEEK, 2);
  assert.deepEqual(r.segments, [seg('a', 2, 2, 0), seg('b', 2, 2, 1)]);
  assert.deepEqual(r.hidden, [0, 0, 1, 0, 0, 0, 0]);
});
test('maxLanes: a hidden multi-day event is counted on every column it covers', () => {
  const r = run([
    ev('a', '2026-03-02', '2026-03-04'),
    ev('b', '2026-03-02', '2026-03-04'),
    ev('c', '2026-03-02', '2026-03-04'),
  ], WEEK, 2);
  assert.deepEqual(r.segments.map((s) => s.id), ['a', 'b']);
  assert.deepEqual(r.hidden, [0, 1, 1, 1, 0, 0, 0]);
});
test('maxLanes does not change how the remaining events are assigned', () => {
  const r = run([
    ev('a', '2026-03-01', '2026-03-03'), // 0-2 lane 0
    ev('b', '2026-03-02'), // 1-1 lane 1 -> hidden
    ev('c', '2026-03-04', '2026-03-05'), // 3-4 lane 0
  ], WEEK, 1);
  assert.deepEqual(r.segments, [seg('a', 0, 2, 0), seg('c', 3, 4, 0)]);
  assert.deepEqual(r.hidden, [0, 1, 0, 0, 0, 0, 0]);
});
test('maxLanes = 0 hides everything', () => {
  const r = run([ev('a', '2026-03-02', '2026-03-03')], WEEK, 0);
  assert.deepEqual(r, { segments: [], hidden: [0, 1, 1, 0, 0, 0, 0] });
});
test('works for a Monday week start', () => {
  const r = run([ev('a', '2026-03-01', '2026-03-02'), ev('b', '2026-03-08')], '2026-03-02');
  assert.deepEqual(r.segments, [seg('a', 0, 0, 0, true, false), seg('b', 6, 6, 0)]);
});
test('DST week (2026-03-08 is a 23-hour day in New York)', () => {
  const r = run([
    ev('a', '2026-03-08', '2026-03-10'),
    ev('b', '2026-03-09', '2026-03-14'),
    ev('c', '2026-03-14'),
    ev('d', '2026-03-15'),
  ], '2026-03-08');
  assert.deepEqual(r.segments, [seg('a', 0, 2, 0), seg('b', 1, 6, 1), seg('c', 6, 6, 0)]);
});
test('year boundary', () => {
  const r = run([ev('a', '2026-12-31', '2027-01-02')], '2026-12-27');
  assert.deepEqual(r.segments, [seg('a', 4, 6, 0, false, false)]);
});
test('does not mutate its input', () => {
  const events = Object.freeze([Object.freeze(ev('a', '2026-03-02', '2026-03-03'))]);
  const a = layoutWeekSpans(events, WEEK);
  const b = layoutWeekSpans(events, WEEK);
  assert.deepEqual(a, b);
  assert.notEqual(a.segments, b.segments);
});
