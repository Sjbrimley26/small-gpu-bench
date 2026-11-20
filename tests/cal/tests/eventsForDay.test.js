import { test } from 'node:test';
import assert from 'node:assert/strict';
import { eventsForDay } from '../app/src/gen/eventsForDay.js';

const ev = (id, title, start, end, allDay = false) => ({ id, title, start, end, allDay, color: null });
const D = (...a) => new Date(...a);
const day = D(2026, 9, 19);

test('includes a timed event inside the day', () => {
  const r = eventsForDay([ev(1, 'A', D(2026, 9, 19, 9), D(2026, 9, 19, 10))], day);
  assert.deepEqual(r.map((e) => e.id), [1]);
});

test('excludes events on other days', () => {
  const r = eventsForDay([
    ev(1, 'Before', D(2026, 9, 18, 9), D(2026, 9, 18, 10)),
    ev(2, 'After', D(2026, 9, 20, 9), D(2026, 9, 20, 10)),
  ], day);
  assert.deepEqual(r, []);
});

test('timed event ending exactly at midnight does not leak into the next day', () => {
  const r = eventsForDay([ev(1, 'Late', D(2026, 9, 18, 22), D(2026, 9, 19, 0))], day);
  assert.deepEqual(r, []);
});

test('timed event crossing midnight appears on both days', () => {
  const e = ev(1, 'Overnight', D(2026, 9, 18, 22), D(2026, 9, 19, 2));
  assert.equal(eventsForDay([e], D(2026, 9, 18)).length, 1);
  assert.equal(eventsForDay([e], day).length, 1);
});

test('zero-length timed event is included on the day it starts', () => {
  const e = ev(1, 'Ping', D(2026, 9, 19, 0), D(2026, 9, 19, 0));
  assert.equal(eventsForDay([e], day).length, 1);
  assert.equal(eventsForDay([e], D(2026, 9, 18)).length, 0);
});

test('all-day events: end date is inclusive', () => {
  const trip = ev(1, 'Trip', D(2026, 9, 18), D(2026, 9, 20), true);
  assert.equal(eventsForDay([trip], D(2026, 9, 17)).length, 0);
  assert.equal(eventsForDay([trip], D(2026, 9, 18)).length, 1);
  assert.equal(eventsForDay([trip], D(2026, 9, 20)).length, 1);
  assert.equal(eventsForDay([trip], D(2026, 9, 21)).length, 0);
});

test('works when `day` has a time component', () => {
  const r = eventsForDay([ev(1, 'A', D(2026, 9, 19, 9), D(2026, 9, 19, 10))], D(2026, 9, 19, 23, 30));
  assert.equal(r.length, 1);
});

test('sorted: all-day first, then by start, then by title', () => {
  const r = eventsForDay([
    ev(1, 'Zed', D(2026, 9, 19, 9), D(2026, 9, 19, 10)),
    ev(2, 'Alpha', D(2026, 9, 19, 9), D(2026, 9, 19, 11)),
    ev(3, 'Early', D(2026, 9, 19, 7), D(2026, 9, 19, 8)),
    ev(4, 'Holiday', D(2026, 9, 19), D(2026, 9, 19), true),
  ], day);
  assert.deepEqual(r.map((e) => e.id), [4, 3, 2, 1]);
});

test('does not mutate the input array', () => {
  const list = [
    ev(1, 'B', D(2026, 9, 19, 10), D(2026, 9, 19, 11)),
    ev(2, 'A', D(2026, 9, 19, 9), D(2026, 9, 19, 10)),
  ];
  eventsForDay(list, day);
  assert.deepEqual(list.map((e) => e.id), [1, 2]);
});
