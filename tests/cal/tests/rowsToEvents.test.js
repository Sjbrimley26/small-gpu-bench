import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rowsToEvents } from '../app/src/gen/rowsToEvents.js';

const FM = { title: 'Name', start: 'Start', end: 'End', color: 'Category' };

test('timed ISO row becomes a timed event', () => {
  const [e] = rowsToEvents([{ id: 1, Name: 'Standup', Start: '2026-10-19T16:00:00Z', End: '2026-10-19T16:30:00Z' }], FM);
  assert.equal(e.id, 1);
  assert.equal(e.title, 'Standup');
  assert.equal(e.allDay, false);
  assert.equal(e.start.getTime(), Date.parse('2026-10-19T16:00:00Z'));
  assert.equal(e.end.getTime(), Date.parse('2026-10-19T16:30:00Z'));
});

test('date-only start makes an all-day event at local midnight', () => {
  const [e] = rowsToEvents([{ id: 2, Name: 'Trip', Start: '2026-10-20', End: '2026-10-22' }], FM);
  assert.equal(e.allDay, true);
  assert.deepEqual([e.start.getFullYear(), e.start.getMonth(), e.start.getDate(), e.start.getHours()], [2026, 9, 20, 0]);
  assert.deepEqual([e.end.getFullYear(), e.end.getMonth(), e.end.getDate(), e.end.getHours()], [2026, 9, 22, 0]);
});

test('missing end: timed events last 1 hour, all-day events end the same day', () => {
  const evs = rowsToEvents([
    { id: 3, Name: 'Call', Start: '2026-10-19T18:00:00Z', End: null },
    { id: 4, Name: 'Holiday', Start: '2026-10-21' },
  ], FM);
  const call = evs.find((e) => e.id === 3);
  const hol = evs.find((e) => e.id === 4);
  assert.equal(call.end.getTime() - call.start.getTime(), 60 * 60 * 1000);
  assert.equal(hol.end.getTime(), hol.start.getTime());
});

test('end before start is clamped to start', () => {
  const [e] = rowsToEvents([{ id: 5, Name: 'Oops', Start: '2026-10-19T18:00:00Z', End: '2026-10-19T17:00:00Z' }], FM);
  assert.equal(e.end.getTime(), e.start.getTime());
});

test('rows with a missing, empty or invalid start are skipped', () => {
  const evs = rowsToEvents([
    { id: 6, Name: 'No start' },
    { id: 7, Name: 'Empty', Start: '' },
    { id: 8, Name: 'Garbage', Start: 'not a date' },
    { id: 9, Name: 'Good', Start: '2026-10-19T18:00:00Z' },
  ], FM);
  assert.deepEqual(evs.map((e) => e.id), [9]);
});

test('title: trimmed, and "(untitled)" when missing or blank', () => {
  const evs = rowsToEvents([
    { id: 10, Name: '  Lunch  ', Start: '2026-10-19T18:00:00Z' },
    { id: 11, Name: '   ', Start: '2026-10-19T19:00:00Z' },
    { id: 12, Start: '2026-10-19T20:00:00Z' },
  ], FM);
  assert.deepEqual(evs.map((e) => e.title), ['Lunch', '(untitled)', '(untitled)']);
});

test('color: single-select object uses .color, plain string is kept, otherwise null', () => {
  const evs = rowsToEvents([
    { id: 13, Name: 'A', Start: '2026-10-19T01:00:00Z', Category: { id: 1, value: 'Work', color: 'light-blue' } },
    { id: 14, Name: 'B', Start: '2026-10-19T02:00:00Z', Category: '#ff0000' },
    { id: 15, Name: 'C', Start: '2026-10-19T03:00:00Z', Category: null },
    { id: 16, Name: 'D', Start: '2026-10-19T04:00:00Z' },
  ], FM);
  assert.deepEqual(evs.map((e) => e.color), ['light-blue', '#ff0000', null, null]);
});

test('fieldMap without end/color still works', () => {
  const evs = rowsToEvents([{ id: 17, Title: 'X', When: '2026-10-19T18:00:00Z', End: '2026-10-19T20:00:00Z' }], { title: 'Title', start: 'When' });
  assert.equal(evs.length, 1);
  assert.equal(evs[0].end.getTime() - evs[0].start.getTime(), 60 * 60 * 1000);
  assert.equal(evs[0].color, null);
});

test('output is sorted by start time', () => {
  const evs = rowsToEvents([
    { id: 20, Name: 'Late', Start: '2026-10-19T20:00:00Z' },
    { id: 21, Name: 'Early', Start: '2026-10-19T08:00:00Z' },
    { id: 22, Name: 'Mid', Start: '2026-10-19T12:00:00Z' },
  ], FM);
  assert.deepEqual(evs.map((e) => e.id), [21, 22, 20]);
});

test('event objects have exactly the documented keys', () => {
  const [e] = rowsToEvents([{ id: 30, Name: 'K', Start: '2026-10-19T18:00:00Z' }], FM);
  assert.deepEqual(Object.keys(e).sort(), ['allDay', 'color', 'end', 'id', 'start', 'title']);
  assert.ok(e.start instanceof Date && e.end instanceof Date);
});
