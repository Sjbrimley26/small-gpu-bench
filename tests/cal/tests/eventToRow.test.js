import { test } from 'node:test';
import assert from 'node:assert/strict';
import { eventToRow } from '../app/src/gen/eventToRow.js';

const FM = { title: 'Name', start: 'Start', end: 'End', color: 'Category' };

test('timed event uses ISO strings in UTC', () => {
  const start = new Date(Date.UTC(2026, 9, 19, 16, 0));
  const end = new Date(Date.UTC(2026, 9, 19, 17, 30));
  const row = eventToRow({ title: 'Standup', start, end, allDay: false }, FM);
  assert.deepEqual(row, { Name: 'Standup', Start: '2026-10-19T16:00:00.000Z', End: '2026-10-19T17:30:00.000Z' });
});

test('all-day event uses local YYYY-MM-DD', () => {
  const row = eventToRow({ title: 'Trip', start: new Date(2026, 9, 20), end: new Date(2026, 9, 22), allDay: true }, FM);
  assert.deepEqual(row, { Name: 'Trip', Start: '2026-10-20', End: '2026-10-22' });
});

test('title is trimmed', () => {
  const row = eventToRow({ title: '  Gym  ', start: new Date(2026, 0, 5), end: null, allDay: true }, FM);
  assert.equal(row.Name, 'Gym');
});

test('end is omitted when draft.end is null or fieldMap has no end', () => {
  const a = eventToRow({ title: 'A', start: new Date(2026, 0, 5), end: null, allDay: true }, FM);
  assert.deepEqual(Object.keys(a).sort(), ['Name', 'Start']);
  const b = eventToRow({ title: 'B', start: new Date(2026, 0, 5), end: new Date(2026, 0, 6), allDay: true }, { title: 'T', start: 'S' });
  assert.deepEqual(b, { T: 'B', S: '2026-01-05' });
});

test('never writes the color field', () => {
  const row = eventToRow({ title: 'C', start: new Date(2026, 0, 5), end: new Date(2026, 0, 5), allDay: true, color: 'red' }, FM);
  assert.equal('Category' in row, false);
});
