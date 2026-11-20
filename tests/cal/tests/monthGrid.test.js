import { test } from 'node:test';
import assert from 'node:assert/strict';
import { monthGrid } from '../app/src/gen/monthGrid.js';

const ymd = (d) => [d.getFullYear(), d.getMonth() + 1, d.getDate()];

test('returns exactly 42 Date objects', () => {
  const g = monthGrid(2026, 9, 0);
  assert.equal(g.length, 42);
  for (const d of g) assert.ok(d instanceof Date);
});

test('October 2026, Sunday start: grid starts Sun Sep 27', () => {
  const g = monthGrid(2026, 9, 0); // Oct 1 2026 is a Thursday
  assert.deepEqual(ymd(g[0]), [2026, 9, 27]);
  assert.deepEqual(ymd(g[41]), [2026, 11, 7]);
});

test('October 2026, Monday start: grid starts Mon Sep 28', () => {
  const g = monthGrid(2026, 9, 1);
  assert.deepEqual(ymd(g[0]), [2026, 9, 28]);
  assert.equal(g[0].getDay(), 1);
});

test('February 2026 starts on a Sunday, so the 1st is the first cell', () => {
  const g = monthGrid(2026, 1, 0);
  assert.deepEqual(ymd(g[0]), [2026, 2, 1]);
});

test('days are consecutive and every cell is at midnight', () => {
  const g = monthGrid(2026, 11, 0); // December, crosses into next year
  for (let i = 0; i < g.length; i++) {
    assert.equal(g[i].getHours(), 0, `cell ${i} not at midnight`);
    if (i > 0) {
      const next = new Date(g[i - 1].getFullYear(), g[i - 1].getMonth(), g[i - 1].getDate() + 1);
      assert.deepEqual(ymd(g[i]), ymd(next), `cell ${i} is not the day after cell ${i - 1}`);
    }
  }
});

test('contains every day of the month', () => {
  const g = monthGrid(2026, 9, 0);
  const inMonth = g.filter((d) => d.getMonth() === 9).map((d) => d.getDate());
  assert.deepEqual(inMonth, Array.from({ length: 31 }, (_, i) => i + 1));
});
