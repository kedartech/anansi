// Pure helpers for the realism views: sorting, counting and the plain-words
// lines. No DOM here, and no figure that reads as a chance of anything.

import { worldDate } from './rules.js';

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const CLASSIFICATION = Object.freeze({
  open: 'Open',
  restricted: 'Restricted',
  confidential: 'Confidential',
  secret: 'Secret',
  most_secret: 'Most Secret',
});

const CHANNEL = Object.freeze({
  in_person: 'In person',
  telephone: 'Telephone',
  cable: 'Cable',
  letter: 'Letter',
  courier: 'Courier',
  radio: 'Radio',
  press: 'Press',
});

const STEP_DONE = Object.freeze({
  pass: 'passed it on',
  expedite: 'pressed it forward',
  amend: 'amended it',
  refuse: 'refused it',
  stall: 'held it back',
  leak: 'let word of it out',
});

const SETTLED = Object.freeze({ enacted: 'Enacted', refused: 'Refused', lapsed: 'Lapsed', withdrawn: 'Withdrawn' });

function pick(table, value) {
  if (!Object.hasOwn(table, value)) throw new Error(`unknown word: ${value}`);
  return table[value];
}

export const classificationWord = (c) => pick(CLASSIFICATION, c);
export const channelWord = (ch) => pick(CHANNEL, ch);

/** Newest arrival first (or oldest first with 'oldest'); ties keep their given order. Does not mutate. */
export function sortInTray(items, order = 'newest') {
  const sign = order === 'oldest' ? -1 : 1;
  return items
    .map((item, i) => [item, i])
    .sort(([a, i], [b, j]) => (a.arrives < b.arrives ? sign : a.arrives > b.arrives ? -sign : i - j))
    .map(([item]) => item);
}

export function unreadCount(items) {
  return items.filter((d) => d.unread).length;
}

/** The in-tray filters, in the order the bar shows them. */
export const TRAY_FILTERS = Object.freeze([['all', 'All'], ['unread', 'Unread'], ['read', 'Read'], ['flagged', 'Flagged']]);

const KEEP = Object.freeze({
  all: () => true,
  unread: (d) => d.unread,
  read: (d) => !d.unread,
  flagged: (d) => d.flagged,
});

export function filterInTray(items, filter) {
  return items.filter(pick(KEEP, filter));
}

export function trayCounts(items) {
  return Object.fromEntries(TRAY_FILTERS.map(([key]) => [key, filterInTray(items, key).length]));
}

/** "You plan about a week ahead" for 7 days, "about a month" for 28 to 31, else "about N days". */
export function horizonWords(days) {
  if (days === 7) return 'You plan about a week ahead';
  if (days >= 28 && days <= 31) return 'You plan about a month ahead';
  return `You plan about ${days} ${days === 1 ? 'day' : 'days'} ahead`;
}

/** Powers as one sentence: "Commander in chief, veto and executive orders." */
export function powersSentence(powers) {
  const items = powers.map((p) => p.trim().replace(/\.$/, '')).filter(Boolean);
  if (!items.length) return '';
  const joined = items.length > 1 ? `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}` : items[0];
  return `${joined[0].toUpperCase()}${joined.slice(1)}.`;
}

/** "Sam Rayburn: passed it on, 4 Sep 1941" or "Sam Rayburn: has it now". */
export function stepLine(step) {
  if (!step.action) return `${step.person}: has it now`;
  const [y, m, d] = (step.on ?? '').split('-').map(Number);
  const when = step.on ? `, ${d} ${MONTHS_SHORT[m - 1]} ${y}` : '';
  return `${step.person}: ${pick(STEP_DONE, step.action)}${when}`;
}

function shortDate(iso) {
  const [, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}

/** Where an order stands, in words. */
export function trackerLine(order) {
  if (order.status === 'moving') {
    if (order.decision_needed) return 'Awaiting your decision';
    return order.with_person ? `With ${order.with_person} since ${worldDate(order.since)}` : 'Moving';
  }
  const last = [...order.steps].reverse().find((s) => s.on);
  const when = last ? ` ${worldDate(last.on)}` : '';
  return `${pick(SETTLED, order.status)}${when}`;
}

/** "Monday 20 October 1941 · next: Rayburn's count, about 24 Oct · 9 quiet days passed". */
export function paceLine(clock) {
  const [y, m, d] = clock.world_date.split('-').map(Number);
  const weekday = WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  const parts = [`${weekday} ${worldDate(clock.world_date)}`];
  if (clock.waiting_on || clock.next_world_date) {
    const what = clock.waiting_on ?? 'the next advance';
    parts.push(clock.next_world_date ? `next: ${what}, about ${shortDate(clock.next_world_date)}` : `next: ${what}`);
  }
  if (clock.days_collapsed > 0) {
    parts.push(`${clock.days_collapsed} quiet ${clock.days_collapsed === 1 ? 'day' : 'days'} passed`);
  }
  return parts.join(' · ');
}

/** What a player may do at a step they hold (core/realism/pipeline.py `StepAction`), in the order offered. */
export const STEP_ACTIONS = Object.freeze([
  ['pass', 'Pass it on'],
  ['expedite', 'Expedite'],
  ['amend', 'Amend'],
  ['stall', 'Stall'],
  ['leak', 'Leak'],
  ['refuse', 'Refuse'],
]);

/** Names to offer for entity parameters: contacts and principals, once each, sorted. */
export function entityChoices(desk) {
  return [...new Set([...desk.contacts.map((c) => c.person), ...desk.principals])].sort();
}
