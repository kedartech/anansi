// Unit tests for the realism rules, against the fixture. Run: node --test web/test/
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  STEP_ACTIONS, TRAY_FILTERS, channelWord, classificationWord, entityChoices, filterInTray, horizonWords, linesHeading, paceLine,
  powersSentence, sortInTray, stepLine, trackerLine, trayCounts, unreadCount,
} from '../js/realism-rules.js';

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

test('paceLine says nothing is due when only the horizon lies ahead', () => {
  const clock = { world_date: '1941-09-04', next_world_date: '1941-09-18', waiting_on: null, days_collapsed: 0, next_wake_at: null };
  assert.equal(paceLine(clock), 'Thursday 4 September 1941 · nothing due before about 18 Sep');
  assert.doesNotMatch(paceLine(clock), /horizon/i);
});

test('a reply letter sits in the tray and reads as a letter in words', () => {
  const reply = fixture.reply;
  const item = docs.find((d) => d.id === reply.item.id);
  assert.ok(item && item.kind === 'letter' && item.classification === 'confidential');
  assert.equal(channelWord(item.channel), 'Letter');
  assert.equal(item.title, 'On your order: Send a bill to Congress');
  assert.ok(reply.prose && reply.body.every((b) => typeof b.value === 'string'));
  assert.equal(linesHeading(reply.body), 'Particulars');
  assert.equal(linesHeading(fixture.page.body), 'Figures quoted');
  for (const l of [item.title, ...reply.body.map((b) => `${b.label} ${b.value}`)]) assert.doesNotMatch(l, /_/);
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

test('the filter bar counts each tray and the counts agree with the listing', () => {
  assert.deepEqual(trayCounts(docs), { all: 7, unread: 2, read: 5, flagged: 1 });
  assert.equal(fixture.listing.flagged, trayCounts(docs).flagged);
  assert.deepEqual(TRAY_FILTERS.map(([key]) => key), ['all', 'unread', 'read', 'flagged']);
  assert.deepEqual(trayCounts([]), { all: 0, unread: 0, read: 0, flagged: 0 });
});

test('filterInTray keeps the right folders and rejects an unknown filter', () => {
  assert.deepEqual(filterInTray(docs, 'unread').map((d) => d.id), ['d-magic-0919', 'd-whip-1017']);
  assert.deepEqual(filterInTray(docs, 'flagged').map((d) => d.id), ['d-cable-1016']);
  assert.equal(filterInTray(docs, 'read').length, 5);
  assert.equal(filterInTray(docs, 'all').length, docs.length);
  assert.throws(() => filterInTray(docs, 'toString'));
});

test('sortInTray can put the oldest arrival first, keeping ties in order', () => {
  const oldest = sortInTray(docs, 'oldest');
  assert.equal(oldest[0].id, 'd-report-1010');
  for (let i = 1; i < oldest.length; i++) assert.ok(oldest[i - 1].arrives <= oldest[i].arrives);
  const tied = [{ id: 'a', arrives: '1941-10-01' }, { id: 'b', arrives: '1941-10-01' }];
  assert.deepEqual(sortInTray(tied, 'oldest').map((d) => d.id), ['a', 'b']);
  assert.deepEqual(sortInTray(tied).map((d) => d.id), ['a', 'b']);
});

test('the planning horizon is in words', () => {
  assert.equal(horizonWords(7), 'You plan about a week ahead');
  assert.equal(horizonWords(30), 'You plan about a month ahead');
  assert.equal(horizonWords(14), 'You plan about 14 days ahead');
  assert.equal(horizonWords(1), 'You plan about 1 day ahead');
});

test('powers join into one sentence', () => {
  assert.equal(powersSentence(fixture.desk.powers),
    'Commander in Chief of the Army and Navy, sign or veto bills and issue executive orders.');
  assert.equal(powersSentence(['declare war']), 'Declare war.');
  assert.equal(powersSentence(['sign bills.', 'veto bills']), 'Sign bills and veto bills.');
  assert.equal(powersSentence([]), '');
});

test('order steps read in words', () => {
  const steps = orders.find((o) => o.id === 'o-neutrality').steps;
  assert.equal(stepLine(steps[0]), 'Franklin D. Roosevelt: passed it on, 10 Oct 1941');
  assert.equal(stepLine(steps[1]), 'Sam Rayburn: has it now');
});

test('the fixture desk and orders carry words, not keys, for the screen', () => {
  for (const l of fixture.desk.levers) {
    assert.ok(l.label && l.summary);
    for (const p of l.params) {
      assert.ok(p.label);
      for (const c of p.choices) assert.ok(c.label && !c.label.includes('_'));
    }
  }
  for (const o of orders) {
    assert.ok(!o.label.includes('_'));
    for (const t of o.terms) assert.ok(!t.includes('_'));
  }
});
