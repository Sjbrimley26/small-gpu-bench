import { test } from 'node:test';
import assert from 'node:assert/strict';
import { layoutDay } from '../app/src/gen/layoutDay.js';

const ev = (id, start, end, allDay = false) => ({ id, title: `E${id}`, start, end, allDay, color: null });
const at = (h, m = 0) => new Date(2026, 9, 19, h, m);
const day = new Date(2026, 9, 19);
const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 0.02, `${msg}: expected ${b}, got ${a}`);
const byId = (out) => Object.fromEntries(out.map((p) => [p.event.id, p]));

test('single event in a full-day window', () => {
  const [p] = layoutDay([ev(1, at(6), at(12))], day);
  near(p.top, 25, 'top');
  near(p.height, 25, 'height');
  assert.equal(p.column, 0);
  assert.equal(p.columns, 1);
});

test('items keep a reference to the original event object', () => {
  const e = ev(1, at(9), at(10));
  const [p] = layoutDay([e], day);
  assert.equal(p.event, e);
});

test('custom window 08:00-18:00 rescales percentages', () => {
  const [p] = layoutDay([ev(1, at(9), at(11))], day, { startHour: 8, endHour: 18 });
  near(p.top, 10, 'top');
  near(p.height, 20, 'height');
});

test('two overlapping events get two columns', () => {
  const m = byId(layoutDay([ev(1, at(9), at(11)), ev(2, at(10), at(12))], day));
  assert.equal(m[1].column, 0);
  assert.equal(m[2].column, 1);
  assert.equal(m[1].columns, 2);
  assert.equal(m[2].columns, 2);
});

test('back-to-back events do not overlap', () => {
  const m = byId(layoutDay([ev(1, at(9), at(10)), ev(2, at(10), at(11))], day));
  assert.equal(m[1].columns, 1);
  assert.equal(m[2].columns, 1);
  assert.equal(m[2].column, 0);
});

test('transitive cluster reuses a freed column', () => {
  // A 9-11, B 10-12, C 11:30-13 -> A col0, B col1, C col0; all share columns=2
  const m = byId(layoutDay([ev('A', at(9), at(11)), ev('B', at(10), at(12)), ev('C', at(11, 30), at(13))], day));
  assert.deepEqual([m.A.column, m.B.column, m.C.column], [0, 1, 0]);
  assert.deepEqual([m.A.columns, m.B.columns, m.C.columns], [2, 2, 2]);
});

test('separate clusters have independent column counts', () => {
  const m = byId(layoutDay([
    ev(1, at(9), at(10)), ev(2, at(9), at(10)), ev(3, at(9, 30), at(10)),
    ev(4, at(14), at(15)),
  ], day));
  assert.equal(m[1].columns, 3);
  assert.equal(m[4].columns, 1);
  assert.equal(m[4].column, 0);
});

test('longer event goes first when starts tie', () => {
  const m = byId(layoutDay([ev('short', at(9), at(10)), ev('long', at(9), at(12))], day));
  assert.equal(m.long.column, 0);
  assert.equal(m.short.column, 1);
});

test('all-day events are ignored', () => {
  const out = layoutDay([ev(1, day, day, true), ev(2, at(9), at(10))], day);
  assert.deepEqual(out.map((p) => p.event.id), [2]);
});

test('events are clipped to the window; events fully outside are dropped', () => {
  const out = layoutDay([
    ev(1, new Date(2026, 9, 18, 22), at(2)),   // starts the day before
    ev(2, at(7), at(8)),                        // before window
    ev(3, at(17), at(20)),                      // runs past window end
  ], day, { startHour: 8, endHour: 18 });
  const m = byId(out);
  assert.equal(m[1], undefined);
  assert.equal(m[2], undefined);
  near(m[3].top, 90, 'clipped top');
  near(m[3].height, 10, 'clipped height');
});

test('event from the previous evening is clipped at midnight', () => {
  const [p] = layoutDay([ev(1, new Date(2026, 9, 18, 22), at(3))], day);
  near(p.top, 0, 'top');
  near(p.height, 12.5, 'height');
});

test('very short events are treated as 15 minutes long', () => {
  const [p] = layoutDay([ev(1, at(12), at(12))], day);
  near(p.height, (15 / 1440) * 100, 'height');
});

test('output is ordered by start time', () => {
  const out = layoutDay([ev(3, at(15), at(16)), ev(1, at(8), at(9)), ev(2, at(12), at(13))], day);
  assert.deepEqual(out.map((p) => p.event.id), [1, 2, 3]);
});
