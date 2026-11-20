import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMonth } from '../app/src/gen/renderMonth.js';

const ev = (id, title, start, end, allDay = false, color = null) => ({ id, title, start, end, allDay, color });
const today = new Date(2026, 9, 18, 14, 0);
const base = { year: 2026, month: 9, weekStartsOn: 0, events: [], today };

// Returns { "YYYY-MM-DD": { cls, inner } } for every day cell.
function cells(html) {
  const re = /<div class="cal-day(?: ([^"]*))?" data-date="(\d{4}-\d{2}-\d{2})">/g;
  const marks = [...html.matchAll(re)];
  const out = {};
  marks.forEach((m, i) => {
    const end = i + 1 < marks.length ? marks[i + 1].index : html.length;
    out[m[2]] = { cls: (m[1] || '').trim().split(/\s+/).filter(Boolean), inner: html.slice(m.index + m[0].length, end) };
  });
  return { marks, out };
}

test('returns a string wrapped in <div class="cal-month">', () => {
  const html = renderMonth(base);
  assert.equal(typeof html, 'string');
  assert.ok(html.startsWith('<div class="cal-month">'), 'must start with <div class="cal-month">');
  assert.ok(html.trimEnd().endsWith('</div>'));
});

test('7 weekday headers in order, rotated by weekStartsOn', () => {
  const sun = [...renderMonth(base).matchAll(/<div class="cal-dow">(\w+)<\/div>/g)].map((m) => m[1]);
  assert.deepEqual(sun, ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']);
  const mon = [...renderMonth({ ...base, weekStartsOn: 1 }).matchAll(/<div class="cal-dow">(\w+)<\/div>/g)].map((m) => m[1]);
  assert.deepEqual(mon, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']);
});

test('42 day cells with data-date, starting Sun Sep 27', () => {
  const { marks } = cells(renderMonth(base));
  assert.equal(marks.length, 42);
  assert.equal(marks[0][2], '2026-09-27');
  assert.equal(marks[41][2], '2026-11-07');
});

test('other-month and today classes', () => {
  const { out } = cells(renderMonth(base));
  assert.ok(out['2026-09-30'].cls.includes('is-other-month'));
  assert.ok(!out['2026-10-01'].cls.includes('is-other-month'));
  assert.ok(out['2026-10-18'].cls.includes('is-today'));
  assert.ok(!out['2026-10-17'].cls.includes('is-today'));
});

test('each cell shows its day number', () => {
  const { out } = cells(renderMonth(base));
  assert.match(out['2026-10-05'].inner, /<span class="cal-daynum">5<\/span>/);
  assert.match(out['2026-11-01'].inner, /<span class="cal-daynum">1<\/span>/);
});

test('events render in their day with id and color', () => {
  const events = [ev(7, 'Dentist', new Date(2026, 9, 20, 9), new Date(2026, 9, 20, 10), false, 'light-blue')];
  const { out } = cells(renderMonth({ ...base, events }));
  assert.match(out['2026-10-20'].inner, /<div class="cal-event" data-event-id="7" data-color="light-blue">Dentist<\/div>/);
  assert.doesNotMatch(out['2026-10-21'].inner, /cal-event/);
});

test('missing color renders as an empty data-color', () => {
  const events = [ev(8, 'X', new Date(2026, 9, 20, 9), new Date(2026, 9, 20, 10))];
  const { out } = cells(renderMonth({ ...base, events }));
  assert.match(out['2026-10-20'].inner, /data-event-id="8" data-color=""/);
});

test('multi-day all-day events appear on every covered day', () => {
  const events = [ev(9, 'Trip', new Date(2026, 9, 21), new Date(2026, 9, 23), true)];
  const { out } = cells(renderMonth({ ...base, events }));
  for (const k of ['2026-10-21', '2026-10-22', '2026-10-23']) assert.match(out[k].inner, /data-event-id="9"/, k);
  assert.doesNotMatch(out['2026-10-24'].inner, /data-event-id="9"/);
});

test('max 3 events per cell, then "+N more"', () => {
  const events = [1, 2, 3, 4, 5].map((i) => ev(i, `E${i}`, new Date(2026, 9, 22, 8 + i), new Date(2026, 9, 22, 9 + i)));
  const { out } = cells(renderMonth({ ...base, events }));
  const inner = out['2026-10-22'].inner;
  assert.equal([...inner.matchAll(/class="cal-event"/g)].length, 3);
  assert.match(inner, /<div class="cal-more">\+2 more<\/div>/);
  const ids = [...inner.matchAll(/data-event-id="(\d+)"/g)].map((m) => m[1]);
  assert.deepEqual(ids, ['1', '2', '3'], 'shows the first three in eventsForDay order');
});

test('exactly 3 events: no "+N more"', () => {
  const events = [1, 2, 3].map((i) => ev(i, `E${i}`, new Date(2026, 9, 22, 8 + i), new Date(2026, 9, 22, 9 + i)));
  const { out } = cells(renderMonth({ ...base, events }));
  assert.doesNotMatch(out['2026-10-22'].inner, /cal-more/);
});

test('titles are HTML-escaped', () => {
  const events = [ev(10, '<b>Tom & Jerry</b>', new Date(2026, 9, 20, 9), new Date(2026, 9, 20, 10))];
  const html = renderMonth({ ...base, events });
  assert.ok(html.includes('&lt;b&gt;Tom &amp; Jerry&lt;/b&gt;'));
  assert.ok(!html.includes('<b>Tom'));
});
