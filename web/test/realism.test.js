// Unit tests for the realism rules, against the fixture. Run: node --test web/test/
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { STEP_ACTIONS, channelWord, classificationWord, entityChoices, paceLine, sortInTray, trackerLine, unreadCount } from '../js/realism-rules.js';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const fixture = JSON.parse(readFileSync(join(WEB, 'test', 'fixtures', 'realism-seat.json'), 'utf8'));
const docs = fixture.listing.documents;
const orders = fixture.orders.orders;

test('the fixture has the required shape', () => {
  assert.ok(docs.length >= 6);
  assert.ok(docs.some((d) => d.classification === 'most_secret' && d.unread));
  assert.equal(fixture.listing.unread, unreadCount(docs));
  assert.ok(orders.some((o) => o.with_person === 'Sam Rayburn' && o.since === '1941-10-12'));
  assert.ok(orders.some((o) => o.decision_needed));
});

test('sortInTray puts the newest arrival first and does not mutate', () => {
  const shuffled = [...docs].reverse();
  const before = shuffled.map((d) => d.id);
  const sorted = sortInTray(shuffled);
  assert.deepEqual(shuffled.map((d) => d.id), before);
  assert.equal(sorted[0].id, 'd-magic-0919');
  for (let i = 1; i < sorted.length; i++) assert.ok(sorted[i - 1].arrives >= sorted[i].arrives);
});

test('unreadCount counts unread items', () => {
  assert.equal(unreadCount(docs), 2);
  assert.equal(unreadCount([]), 0);
});

test('trackerLine reads each state in words', () => {
  const byId = Object.fromEntries(orders.map((o) => [o.id, o]));
  assert.equal(trackerLine(byId['o-neutrality']), 'With Sam Rayburn since 12 October 1941');
  assert.equal(trackerLine(byId['o-convoy']), 'Awaiting your decision');
  assert.equal(trackerLine(byId['o-lend']), 'Enacted 13 October 1941');
  assert.equal(trackerLine({ status: 'refused', decision_needed: false, steps: [{ on: '1941-11-02' }] }), 'Refused 2 November 1941');
});

test('paceLine names the weekday, what is awaited and the quiet days', () => {
  assert.equal(paceLine(fixture.clock), "Monday 20 October 1941 · next: Rayburn's count, about 24 Oct · 9 quiet days passed");
  assert.equal(paceLine({ world_date: '1941-10-21', next_world_date: null, waiting_on: null, days_collapsed: 1, next_wake_at: null }),
    'Tuesday 21 October 1941 · 1 quiet day passed');
});

test('classification and channel words cover every value and reject strangers', () => {
  assert.equal(classificationWord('most_secret'), 'Most Secret');
  assert.equal(classificationWord('open'), 'Open');
  for (const d of docs) {
    assert.ok(classificationWord(d.classification));
    assert.ok(channelWord(d.channel));
  }
  assert.equal(channelWord('in_person'), 'In person');
  assert.throws(() => classificationWord('toString'));
  assert.throws(() => channelWord('pigeon'));
});

test('realism files never show a percentage or talk of chances', () => {
  const files = ['js/realism-rules.js', 'js/views/realism.js', 'css/realism.css', 'test/fixtures/realism-seat.json'];
  const chance = new RegExp(['od', 'ds'].join(''), 'i');
  for (const f of files) {
    const text = readFileSync(join(WEB, f), 'utf8');
    assert.doesNotMatch(text, /%/, f);
    assert.doesNotMatch(text, chance, f);
  }
});

test('realism output contains no figures that read as a chance', () => {
  const lines = [paceLine(fixture.clock), ...orders.map(trackerLine)];
  for (const l of lines) assert.doesNotMatch(l, /%|\bchance\b|\bprobab/i);
});

test('entity choices are contacts and principals, once each', () => {
  const names = entityChoices({ contacts: [{ person: 'B' }, { person: 'A' }], principals: ['A', 'C'] });
  assert.deepEqual(names, ['A', 'B', 'C']);
});

test('the step actions match the engine and offer each once', () => {
  const values = STEP_ACTIONS.map(([v]) => v).sort();
  assert.deepEqual(values, ['amend', 'expedite', 'leak', 'pass', 'refuse', 'stall']);
});
