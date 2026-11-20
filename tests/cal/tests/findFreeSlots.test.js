import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findFreeSlots } from '../app/src/gen/findFreeSlots.js';

const D = '2026-03-04'; // a Wednesday
const slot = (start, end) => ({ start, end });
const e = (s, en, extra = {}) => ({ start: `${D}T${s}`, end: `${D}T${en}`, ...extra });
const run = (events, opts, date = D) =>
  findFreeSlots(Object.freeze(events.map((x) => Object.freeze(x))), date, opts && Object.freeze(opts));

test('no events: the whole default work window (09:00-17:00)', () => {
  assert.deepEqual(run([]), [slot('09:00', '17:00')]);
});
test('one event in the middle splits the day', () => {
  assert.deepEqual(run([e('12:00', '13:00')]), [slot('09:00', '12:00'), slot('13:00', '17:00')]);
});
test('event overlapping the start of the work day', () => {
  assert.deepEqual(run([e('08:00', '10:00')]), [slot('10:00', '17:00')]);
});
test('event overlapping the end of the work day', () => {
  assert.deepEqual(run([e('16:00', '18:00')]), [slot('09:00', '16:00')]);
});
test('event covering the whole work day leaves nothing', () => {
  assert.deepEqual(run([e('08:00', '18:00')]), []);
});
test('events entirely outside work hours are irrelevant', () => {
  assert.deepEqual(run([e('06:00', '08:00'), e('18:00', '19:00')]), [slot('09:00', '17:00')]);
});
test('minMinutes filters short gaps; a gap of exactly minMinutes is kept', () => {
  const events = [e('09:00', '10:00'), e('10:20', '11:00'), e('11:30', '12:00')]; // gaps: 20, 30, then 300
  assert.deepEqual(run(events), [slot('11:00', '11:30'), slot('12:00', '17:00')]);
  assert.deepEqual(run(events, { minMinutes: 15 }), [slot('10:00', '10:20'), slot('11:00', '11:30'), slot('12:00', '17:00')]);
  assert.deepEqual(run(events, { minMinutes: 31 }), [slot('12:00', '17:00')]);
});
test('overlapping and back-to-back events are merged', () => {
  assert.deepEqual(run([e('10:00', '11:00'), e('10:30', '12:00'), e('12:00', '12:30')]),
    [slot('09:00', '10:00'), slot('12:30', '17:00')]);
});
test('input order does not matter', () => {
  assert.deepEqual(run([e('14:00', '15:00'), e('10:00', '11:00'), e('12:00', '13:00')]),
    [slot('09:00', '10:00'), slot('11:00', '12:00'), slot('13:00', '14:00'), slot('15:00', '17:00')]);
});
test('an event nested inside another does not shorten the busy block', () => {
  assert.deepEqual(run([e('10:00', '12:00'), e('10:30', '11:00')]), [slot('09:00', '10:00'), slot('12:00', '17:00')]);
});
test('buffer pads both sides of every event', () => {
  assert.deepEqual(run([e('12:00', '13:00')], { buffer: 15 }), [slot('09:00', '11:45'), slot('13:15', '17:00')]);
});
test('buffer is clipped to the work window', () => {
  assert.deepEqual(run([e('09:00', '09:30')], { buffer: 15 }), [slot('09:45', '17:00')]);
});
test('buffer can close a gap between two events', () => {
  assert.deepEqual(run([e('10:00', '11:00'), e('11:20', '12:00')], { buffer: 15 }),
    [slot('09:00', '09:45'), slot('12:15', '17:00')]);
});
test('buffers that just touch leave no zero-length slot', () => {
  assert.deepEqual(run([e('10:00', '11:00'), e('11:30', '12:00')], { buffer: 15, minMinutes: 0 }),
    [slot('09:00', '09:45'), slot('12:15', '17:00')]);
});
test('an event just outside the window can still block via its buffer', () => {
  assert.deepEqual(run([e('08:00', '08:50')], { buffer: 15 }), [slot('09:05', '17:00')]);
});
test('all-day events do not block time', () => {
  assert.deepEqual(run([{ start: D, end: D, allDay: true }]), [slot('09:00', '17:00')]);
});
test('events on other days are ignored', () => {
  const other = { start: '2026-03-05T12:00', end: '2026-03-05T13:00' };
  assert.deepEqual(run([other]), [slot('09:00', '17:00')]);
});
test('an event running overnight into the date blocks the morning', () => {
  assert.deepEqual(run([{ start: '2026-03-03T22:00', end: `${D}T10:00` }]), [slot('10:00', '17:00')]);
});
test('an event spanning the entire date blocks everything', () => {
  assert.deepEqual(run([{ start: '2026-03-03T20:00', end: '2026-03-05T08:00' }]), []);
});
test('custom work hours', () => {
  assert.deepEqual(run([e('10:00', '10:30')], { workStart: '08:30', workEnd: '12:00' }),
    [slot('08:30', '10:00'), slot('10:30', '12:00')]);
});
test('work window that is empty or reversed gives []', () => {
  assert.deepEqual(run([], { workStart: '17:00', workEnd: '09:00' }), []);
  assert.deepEqual(run([], { workStart: '09:00', workEnd: '09:00' }), []);
});
test('zero-length and negative-length events are ignored', () => {
  assert.deepEqual(run([e('12:00', '12:00'), e('13:00', '12:30')]), [slot('09:00', '17:00')]);
  assert.deepEqual(run([e('12:00', '12:00')], { buffer: 15 }), [slot('09:00', '17:00')]);
});
test('recurring daily event blocks its slot on later dates, not before its start', () => {
  const daily = { start: '2026-03-02T12:00', end: '2026-03-02T13:00', rule: { freq: 'daily' } };
  assert.deepEqual(run([daily]), [slot('09:00', '12:00'), slot('13:00', '17:00')]);
  assert.deepEqual(run([daily], undefined, '2026-03-01'), [slot('09:00', '17:00')]);
});
test('recurring event: exdates and weekday rules are honoured', () => {
  const base = { start: '2026-03-02T12:00', end: '2026-03-02T13:00' };
  assert.deepEqual(run([{ ...base, rule: { freq: 'daily' }, exdates: [D] }]), [slot('09:00', '17:00')]);
  const mondays = { ...base, rule: { freq: 'weekly', byDay: ['MO'] } };
  assert.deepEqual(run([mondays]), [slot('09:00', '17:00')]); // Wednesday
  assert.deepEqual(run([mondays], undefined, '2026-03-09'), [slot('09:00', '12:00'), slot('13:00', '17:00')]);
});
test('overnight recurring event: the previous evening\'s occurrence blocks this morning', () => {
  const night = { start: '2026-03-01T22:00', end: '2026-03-02T10:00', rule: { freq: 'daily' } };
  assert.deepEqual(run([night]), [slot('10:00', '17:00')]);
  assert.deepEqual(run([night], undefined, '2026-03-01'), [slot('09:00', '17:00')]); // first occurrence starts at 22:00
});
test('overnight recurring event: exdates match the occurrence START date', () => {
  const night = { start: '2026-03-01T22:00', end: '2026-03-02T10:00', rule: { freq: 'daily' }, exdates: ['2026-03-03'] };
  assert.deepEqual(run([night]), [slot('09:00', '17:00')]);
});
test('recurring and one-off events combine', () => {
  const daily = { start: '2026-03-02T09:00', end: '2026-03-02T09:30', rule: { freq: 'daily' } };
  assert.deepEqual(run([daily, e('09:30', '10:00'), e('15:00', '16:00')]),
    [slot('10:00', '15:00'), slot('16:00', '17:00')]);
});
test('does not mutate its input', () => {
  const events = Object.freeze([Object.freeze({ ...e('12:00', '13:00') })]);
  const a = findFreeSlots(events, D);
  const b = findFreeSlots(events, D);
  assert.deepEqual(a, b);
  assert.notEqual(a, b);
});
