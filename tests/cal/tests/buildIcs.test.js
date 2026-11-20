import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildIcs } from '../app/src/gen/buildIcs.js';

const OPTS = { dtstamp: '20260101T000000Z' };
const HEAD = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//calagent//EN\r\nCALSCALE:GREGORIAN\r\n';
const TAIL = 'END:VCALENDAR\r\n';
const ev = (extra) => ({ uid: 'a1@calagent', title: 'T', start: '2026-03-04T09:30', ...extra });

const unfold = (s) => s.replace(/\r\n /g, '');
const physical = (s) => s.split('\r\n').slice(0, -1); // drop the empty piece after the final CRLF
const bytes = (s) => Buffer.byteLength(s, 'utf8');
const loneSurrogate = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

// All physical lines of the first logical line that starts with `prefix`.
function group(out, prefix) {
  const lines = physical(out);
  const i = lines.findIndex((l) => l.startsWith(prefix));
  assert.ok(i >= 0, `no line starting with ${prefix}`);
  const g = [lines[i]];
  for (let j = i + 1; j < lines.length && lines[j].startsWith(' '); j++) g.push(lines[j]);
  return g;
}

test('empty event list still produces a valid calendar', () => {
  assert.equal(buildIcs([], OPTS), HEAD + TAIL);
});
test('minimal timed event, exact output', () => {
  const out = buildIcs([{ uid: 'a1@calagent', title: 'Standup', start: '2026-03-04T09:30', end: '2026-03-04T10:00' }], OPTS);
  assert.equal(out, HEAD +
    'BEGIN:VEVENT\r\nUID:a1@calagent\r\nDTSTAMP:20260101T000000Z\r\n' +
    'DTSTART:20260304T093000\r\nDTEND:20260304T100000\r\nSUMMARY:Standup\r\nEND:VEVENT\r\n' + TAIL);
});
test('timed event without end has no DTEND line', () => {
  const out = buildIcs([ev()], OPTS);
  assert.ok(out.includes('DTSTART:20260304T093000\r\n'));
  assert.ok(!out.includes('DTEND'));
});
test('LOCATION then DESCRIPTION follow SUMMARY; both omitted when absent or empty', () => {
  const full = buildIcs([ev({ location: 'Room 4', description: 'Bring notes' })], OPTS);
  assert.ok(full.includes('SUMMARY:T\r\nLOCATION:Room 4\r\nDESCRIPTION:Bring notes\r\nEND:VEVENT'));
  const none = buildIcs([ev({ location: '', description: undefined })], OPTS);
  assert.ok(!none.includes('LOCATION') && !none.includes('DESCRIPTION'));
});
test('all-day single day: DTEND is the next day (exclusive)', () => {
  const out = buildIcs([ev({ allDay: true, start: '2026-03-07' })], OPTS);
  assert.ok(out.includes('DTSTART;VALUE=DATE:20260307\r\nDTEND;VALUE=DATE:20260308\r\n'));
});
test('all-day multi-day: inclusive end date becomes exclusive DTEND', () => {
  const out = buildIcs([ev({ allDay: true, start: '2026-03-07', end: '2026-03-09' })], OPTS);
  assert.ok(out.includes('DTSTART;VALUE=DATE:20260307\r\nDTEND;VALUE=DATE:20260310\r\n'));
});
test('all-day exclusive end rolls over month, year and leap day', () => {
  const dtend = (end) => buildIcs([ev({ allDay: true, start: '2026-01-01', end })], OPTS).match(/DTEND;VALUE=DATE:(\d+)/)[1];
  assert.equal(dtend('2026-01-31'), '20260201');
  assert.equal(dtend('2026-12-31'), '20270101');
  assert.equal(dtend('2028-02-28'), '20280229');
  assert.equal(dtend('2028-02-29'), '20280301');
  assert.equal(dtend('2027-02-28'), '20270301');
});
test('all-day accepts datetime-looking strings and uses only the date part', () => {
  const out = buildIcs([ev({ allDay: true, start: '2026-03-07T00:00', end: '2026-03-07T23:59' })], OPTS);
  assert.ok(out.includes('DTSTART;VALUE=DATE:20260307\r\nDTEND;VALUE=DATE:20260308\r\n'));
});
test('all-day with an end before the start is treated as a single day', () => {
  const out = buildIcs([ev({ allDay: true, start: '2026-03-07', end: '2026-03-01' })], OPTS);
  assert.ok(out.includes('DTEND;VALUE=DATE:20260308\r\n'));
});
test('text escaping: backslash, semicolon, comma', () => {
  const out = buildIcs([ev({ title: 'Lunch, with Bob; a\\b' })], OPTS);
  assert.ok(out.includes('SUMMARY:Lunch\\, with Bob\\; a\\\\b\r\n'));
});
test('text escaping: newlines (LF, CRLF) become a literal backslash-n', () => {
  const out = buildIcs([ev({ description: 'line1\nline2\r\nline3' })], OPTS);
  assert.ok(out.includes('DESCRIPTION:line1\\nline2\\nline3\r\n'));
});
test('UID, DTSTAMP and PRODID are not escaped', () => {
  const out = buildIcs([ev({ uid: 'abc-123@example.com' })], { dtstamp: '20260101T000000Z', prodId: '-//Acme//Cal 1.0//EN' });
  assert.ok(out.includes('PRODID:-//Acme//Cal 1.0//EN\r\n'));
  assert.ok(out.includes('UID:abc-123@example.com\r\n'));
});
test('ASCII folding: 75-octet lines, continuation lines start with one space', () => {
  const out = buildIcs([ev({ description: 'x'.repeat(200) })], OPTS);
  const g = group(out, 'DESCRIPTION:');
  assert.deepEqual(g.map(bytes), [75, 75, 64]);
  assert.ok(g.slice(1).every((l) => l.startsWith(' ') && !l.startsWith('  ')));
  assert.equal(unfold(g.join('\r\n')), 'DESCRIPTION:' + 'x'.repeat(200));
});
test('a line of exactly 75 octets is not folded; 76 is', () => {
  const at75 = buildIcs([ev({ description: 'y'.repeat(63) })], OPTS);
  assert.deepEqual(group(at75, 'DESCRIPTION:').map(bytes), [75]);
  const at76 = buildIcs([ev({ description: 'y'.repeat(64) })], OPTS);
  assert.deepEqual(group(at76, 'DESCRIPTION:').map(bytes), [75, 2]);
});
test('folding counts UTF-8 octets, not characters (2-, 3- and 4-byte characters)', () => {
  for (const unit of ['é', '日', '😀']) {
    const text = unit.repeat(60);
    const out = buildIcs([ev({ description: text })], OPTS);
    const g = group(out, 'DESCRIPTION:');
    assert.ok(g.length > 1, `${unit} should fold`);
    g.forEach((l) => assert.ok(bytes(l) <= 75, `${unit}: line has ${bytes(l)} octets`));
    // not wastefully short: every line but the last carries at least 72 octets
    g.slice(0, -1).forEach((l) => assert.ok(bytes(l) >= 72, `${unit}: line only ${bytes(l)} octets`));
    assert.equal(unfold(g.join('\r\n')), 'DESCRIPTION:' + text);
    assert.ok(!loneSurrogate.test(out), `${unit}: split a surrogate pair`);
  }
});
test('folding happens after escaping', () => {
  const out = buildIcs([ev({ title: ','.repeat(80) })], OPTS);
  const g = group(out, 'SUMMARY:');
  g.forEach((l) => assert.ok(bytes(l) <= 75));
  assert.equal(unfold(g.join('\r\n')), 'SUMMARY:' + '\\,'.repeat(80));
});
test('long LOCATION folds too, and never leaves an empty continuation line', () => {
  const out = buildIcs([ev({ location: 'z'.repeat(137) })], OPTS);
  physical(out).forEach((l) => assert.ok(l !== ' ' && l !== ''));
  physical(out).forEach((l) => assert.ok(bytes(l) <= 75));
});
test('line endings: every LF is preceded by CR; output ends with END:VCALENDAR CRLF', () => {
  const out = buildIcs([ev({ description: 'a\nb', location: 'q'.repeat(150) })], OPTS);
  assert.ok(!/(?<!\r)\n/.test(out));
  assert.ok(out.endsWith('END:VCALENDAR\r\n'));
});
test('several events keep their order, one VEVENT block each', () => {
  const out = buildIcs([ev({ uid: 'one', title: 'First' }), ev({ uid: 'two', title: 'Second' }), ev({ uid: 'three', title: 'Third' })], OPTS);
  assert.equal(out.match(/BEGIN:VEVENT/g).length, 3);
  assert.equal(out.match(/END:VEVENT/g).length, 3);
  assert.ok(out.indexOf('UID:one') < out.indexOf('UID:two') && out.indexOf('UID:two') < out.indexOf('UID:three'));
});
test('does not mutate its input', () => {
  const events = Object.freeze([Object.freeze(ev({ description: 'a,b' }))]);
  const first = buildIcs(events, Object.freeze({ ...OPTS }));
  assert.equal(buildIcs(events, OPTS), first);
});
