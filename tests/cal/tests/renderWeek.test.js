import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderWeek } from '../app/src/gen/renderWeek.js';

const ev = (id, title, start, end, allDay = false, color = null) => ({ id, title, start, end, allDay, color });
const today = new Date(2026, 9, 18, 9);
const anchor = new Date(2026, 9, 21, 13); // Wed Oct 21
const base = { anchor, weekStartsOn: 0, events: [], today, startHour: 0, endHour: 24 };

function blocks(html, cls) {
  const re = new RegExp(`<div class="${cls}(?: ([^"]*))?" data-date="(\\d{4}-\\d{2}-\\d{2})">`, 'g');
  const marks = [...html.matchAll(re)];
  return marks.map((m, i) => {
    let end = i + 1 < marks.length ? marks[i + 1].index : html.length;
    return { date: m[2], cls: (m[1] || '').trim(), inner: html.slice(m.index + m[0].length, end) };
  });
}
// inner of a cal-col block up to the next column-level element
const colInner = (html, date) => blocks(html, 'cal-col').find((b) => b.date === date).inner;
const pct = (s, k) => Number(new RegExp(`${k}:(-?[\\d.]+)%`).exec(s)[1]);

test('wrapper and --days custom property', () => {
  const html = renderWeek(base);
  assert.ok(html.startsWith('<div class="cal-week" style="--days:7">'));
  const one = renderWeek({ ...base, days: 1 });
  assert.ok(one.startsWith('<div class="cal-week" style="--days:1">'));
});

test('7-day view starts on the week start containing anchor', () => {
  const html = renderWeek(base);
  const heads = blocks(html, 'cal-col-head');
  assert.deepEqual(heads.map((h) => h.date),
    ['2026-10-18', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-24']);
  const mon = blocks(renderWeek({ ...base, weekStartsOn: 1 }), 'cal-col-head');
  assert.equal(mon[0].date, '2026-10-19');
});

test('1-day view shows just the anchor day', () => {
  const html = renderWeek({ ...base, days: 1 });
  assert.deepEqual(blocks(html, 'cal-col-head').map((h) => h.date), ['2026-10-21']);
  assert.deepEqual(blocks(html, 'cal-col').map((h) => h.date), ['2026-10-21']);
  assert.deepEqual(blocks(html, 'cal-allday').map((h) => h.date), ['2026-10-21']);
});

test('column headers: weekday, day number, is-today', () => {
  const heads = blocks(renderWeek(base), 'cal-col-head');
  assert.equal(heads[0].cls, 'is-today');
  assert.equal(heads[1].cls, '');
  assert.match(heads[1].inner, /^<span class="cal-dow">Mon<\/span> <span class="cal-daynum">19<\/span><\/div>/);
});

test('hour gutter covers startHour..endHour-1', () => {
  const html = renderWeek({ ...base, startHour: 8, endHour: 18 });
  const hours = [...html.matchAll(/<div class="cal-hour">(\d\d:\d\d)<\/div>/g)].map((m) => m[1]);
  assert.equal(hours.length, 10);
  assert.equal(hours[0], '08:00');
  assert.equal(hours[9], '17:00');
});

test('all-day events go in the all-day row only', () => {
  const events = [ev(1, 'Holiday', new Date(2026, 9, 20), new Date(2026, 9, 20), true)];
  const html = renderWeek({ ...base, events });
  const ad = blocks(html, 'cal-allday').find((b) => b.date === '2026-10-20');
  assert.ok(ad.inner.startsWith('<div class="cal-event" data-event-id="1" data-color="">Holiday</div>'));
  assert.doesNotMatch(colInner(html, '2026-10-20'), /data-event-id="1"/);
});

test('timed event is positioned inside its day column', () => {
  const events = [ev(2, 'Lunch', new Date(2026, 9, 21, 12), new Date(2026, 9, 21, 13), false, 'red')];
  const html = renderWeek({ ...base, startHour: 8, endHour: 18, events });
  const inner = colInner(html, '2026-10-21');
  const m = /<div class="cal-event" data-event-id="2" data-color="red" style="([^"]+)">/.exec(inner);
  assert.ok(m, 'event div with exact attribute order not found in Oct 21 column');
  assert.ok(Math.abs(pct(m[1], 'top') - 40) < 0.02);
  assert.ok(Math.abs(pct(m[1], 'height') - 10) < 0.02);
  assert.equal(pct(m[1], 'left'), 0);
  assert.equal(pct(m[1], 'width'), 100);
  assert.ok(inner.includes('<span class="cal-time">12:00</span> Lunch</div>'));
  assert.doesNotMatch(colInner(html, '2026-10-22'), /data-event-id="2"/);
});

test('overlapping events split the column width', () => {
  const events = [
    ev(3, 'A', new Date(2026, 9, 21, 9), new Date(2026, 9, 21, 11)),
    ev(4, 'B', new Date(2026, 9, 21, 10), new Date(2026, 9, 21, 12)),
  ];
  const inner = colInner(renderWeek({ ...base, events }), '2026-10-21');
  const sA = /data-event-id="3" data-color="" style="([^"]+)"/.exec(inner)[1];
  const sB = /data-event-id="4" data-color="" style="([^"]+)"/.exec(inner)[1];
  assert.equal(pct(sA, 'left'), 0);
  assert.equal(pct(sA, 'width'), 50);
  assert.equal(pct(sB, 'left'), 50);
  assert.equal(pct(sB, 'width'), 50);
});

test('three-way overlap uses 33.33 widths', () => {
  const events = [1, 2, 3].map((i) => ev(10 + i, `E${i}`, new Date(2026, 9, 21, 9), new Date(2026, 9, 21, 10)));
  const inner = colInner(renderWeek({ ...base, events }), '2026-10-21');
  const styles = [...inner.matchAll(/style="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(styles.length, 3);
  assert.deepEqual(styles.map((s) => pct(s, 'width')), [33.33, 33.33, 33.33]);
  assert.deepEqual(styles.map((s) => pct(s, 'left')).sort((a, b) => a - b), [0, 33.33, 66.67]);
});

test('overnight event shows in both day columns', () => {
  const events = [ev(5, 'Night', new Date(2026, 9, 21, 22), new Date(2026, 9, 22, 2))];
  const html = renderWeek({ ...base, events });
  assert.match(colInner(html, '2026-10-21'), /data-event-id="5"/);
  assert.match(colInner(html, '2026-10-22'), /data-event-id="5"/);
});

test('titles are HTML-escaped', () => {
  const events = [ev(6, 'Q&A <live>', new Date(2026, 9, 21, 9), new Date(2026, 9, 21, 10))];
  const html = renderWeek({ ...base, events });
  assert.ok(html.includes('Q&amp;A &lt;live&gt;'));
});
