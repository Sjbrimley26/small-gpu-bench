import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderAgenda } from '../app/src/gen/renderAgenda.js';

const ev = (id, title, start, end, allDay = false, color = null) => ({ id, title, start, end, allDay, color });
const today = new Date(2026, 9, 18, 9);
const from = new Date(2026, 9, 18, 15, 45); // time part must be ignored

function sections(html) {
  const re = /<section class="cal-agenda-day(?: ([^"]*))?" data-date="(\d{4}-\d{2}-\d{2})">/g;
  const marks = [...html.matchAll(re)];
  return marks.map((m, i) => ({
    date: m[2],
    cls: (m[1] || '').trim(),
    inner: html.slice(m.index + m[0].length, i + 1 < marks.length ? marks[i + 1].index : html.length),
  }));
}

test('no events: wrapper with "No events"', () => {
  const html = renderAgenda({ from, days: 14, events: [], today });
  assert.equal(html, '<div class="cal-agenda"><p class="cal-empty">No events</p></div>');
});

test('only days with events get a section, in date order', () => {
  const events = [
    ev(2, 'Later', new Date(2026, 9, 25, 10), new Date(2026, 9, 25, 11)),
    ev(1, 'Soon', new Date(2026, 9, 19, 10), new Date(2026, 9, 19, 11)),
  ];
  const s = sections(renderAgenda({ from, days: 14, events, today }));
  assert.deepEqual(s.map((x) => x.date), ['2026-10-19', '2026-10-25']);
});

test('range is `days` days starting at the day of `from`', () => {
  const events = [
    ev(1, 'Before', new Date(2026, 9, 17, 10), new Date(2026, 9, 17, 11)),
    ev(2, 'First', new Date(2026, 9, 18, 8), new Date(2026, 9, 18, 9)),
    ev(3, 'Last', new Date(2026, 9, 20, 10), new Date(2026, 9, 20, 11)),
    ev(4, 'Outside', new Date(2026, 9, 21, 10), new Date(2026, 9, 21, 11)),
  ];
  const s = sections(renderAgenda({ from, days: 3, events, today }));
  assert.deepEqual(s.map((x) => x.date), ['2026-10-18', '2026-10-20']);
});

test('days defaults to 14', () => {
  const events = [
    ev(1, 'In', new Date(2026, 9, 31, 10), new Date(2026, 9, 31, 11)),
    ev(2, 'Out', new Date(2026, 10, 1, 10), new Date(2026, 10, 1, 11)),
  ];
  const s = sections(renderAgenda({ from, events, today }));
  assert.deepEqual(s.map((x) => x.date), ['2026-10-31']);
});

test('heading format "Mon Oct 19" and is-today class', () => {
  const events = [
    ev(1, 'A', new Date(2026, 9, 18, 10), new Date(2026, 9, 18, 11)),
    ev(2, 'B', new Date(2026, 9, 19, 10), new Date(2026, 9, 19, 11)),
  ];
  const s = sections(renderAgenda({ from, days: 7, events, today }));
  assert.equal(s[0].cls, 'is-today');
  assert.equal(s[1].cls, '');
  assert.match(s[0].inner, /<h3>Sun Oct 18<\/h3>/);
  assert.match(s[1].inner, /<h3>Mon Oct 19<\/h3>/);
});

test('event row markup for timed and all-day events', () => {
  const events = [
    ev(5, 'Standup', new Date(2026, 9, 19, 9, 5), new Date(2026, 9, 19, 9, 20), false, 'green'),
    ev(6, 'Holiday', new Date(2026, 9, 19), new Date(2026, 9, 19), true),
  ];
  const [s] = sections(renderAgenda({ from, days: 7, events, today }));
  assert.ok(s.inner.includes('<div class="cal-event" data-event-id="6" data-color=""><span class="cal-time">All day</span> <span class="cal-title">Holiday</span></div>'),
    'all-day row markup');
  assert.ok(s.inner.includes('<div class="cal-event" data-event-id="5" data-color="green"><span class="cal-time">09:05–09:20</span> <span class="cal-title">Standup</span></div>'),
    'timed row markup (uses an en dash)');
  assert.ok(s.inner.indexOf('data-event-id="6"') < s.inner.indexOf('data-event-id="5"'), 'all-day first');
});

test('multi-day all-day event listed on each day', () => {
  const events = [ev(7, 'Trip', new Date(2026, 9, 19), new Date(2026, 9, 21), true)];
  const s = sections(renderAgenda({ from, days: 7, events, today }));
  assert.deepEqual(s.map((x) => x.date), ['2026-10-19', '2026-10-20', '2026-10-21']);
});

test('titles are HTML-escaped', () => {
  const events = [ev(8, 'R&D <sync>', new Date(2026, 9, 19, 9), new Date(2026, 9, 19, 10))];
  const html = renderAgenda({ from, days: 7, events, today });
  assert.ok(html.includes('R&amp;D &lt;sync&gt;'));
});
