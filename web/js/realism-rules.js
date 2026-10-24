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
  if (clock.waiting_on) {
    parts.push(clock.next_world_date ? `next: ${clock.waiting_on}, about ${shortDate(clock.next_world_date)}` : `next: ${clock.waiting_on}`);
  } else if (clock.next_world_date) {
    parts.push(`nothing due before about ${shortDate(clock.next_world_date)}`);
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

/** How a directive travels, as the form offers it: a letter to someone at hand is a memo. */
export function directiveChannel(ch) {
  return ch === 'letter' ? 'Memo' : channelWord(ch);
}

/** The line above a directive's reading: who it goes to and how. */
export function addressLine(p) {
  const how = directiveChannel(p.channel).toLowerCase();
  return `To ${p.to}, ${p.channel === 'in_person' ? how : `by ${how}`}`;
}

/** Names to offer for entity parameters: contacts and principals, once each, sorted. */
export function entityChoices(desk) {
  return [...new Set([...desk.contacts.map((c) => c.person), ...desk.principals])].sort();
}

/** What a page's structured lines are called: figures when they are numbers, particulars when they are words. */
export function linesHeading(lines) {
  return lines.some((b) => typeof b.value === 'string') ? 'Particulars' : 'Figures quoted';
}

const NUMBER = new Intl.NumberFormat('en-US');

/** "209,835 GRT" or "56 percent"; null for a gap, which the board says in words. */
export function figureValue(value, unit) {
  if (value === null) return null;
  return `${NUMBER.format(value)}${unit ? ` ${unit}` : ''}`;
}

const TREND = Object.freeze({ up: 'Up', down: 'Down', level: 'Unchanged' });

/** The latest figure against the one given before it: "Up from 55", "Unchanged", "First figure given". */
export function trendLine(source) {
  if (source.value === null) return '';
  if (!source.trend) return 'First figure given';
  if (source.trend === 'level') return pick(TREND, 'level');
  const given = source.history.filter((r) => r.value !== null);
  const before = given.length > 1 ? given[given.length - 2] : null;
  return `${pick(TREND, source.trend)}${before ? ` from ${NUMBER.format(before.value)}` : ''}`;
}

/** A gap in words, with the date a figure was last given; never an estimate. */
export function gapLine(source) {
  return source.last_given ? `No figure given; last given ${worldDate(source.last_given)}` : 'No figure given yet';
}

/** "Reported by Sam Rayburn, 13 October 1941; arrived the same day". */
export function reportLine(source) {
  const arrived = source.arrives === source.issued ? 'arrived the same day' : `arrived ${worldDate(source.arrives)}`;
  return `Reported by ${source.author}, ${worldDate(source.issued)}; ${arrived}`;
}

/** The recent reports, oldest first: "55 · 56 · no figure · 56". */
export function runLine(history) {
  return history.map((r) => (r.value === null ? 'no figure' : NUMBER.format(r.value))).join(' · ');
}

/** The host's line while the cast is being chosen: who is still to pick. */
export function castLine(waiting) {
  if (!waiting) return 'Everyone has chosen. You can begin.';
  return waiting === 1 ? 'Waiting for 1 player to choose.' : `Waiting for ${waiting} players to choose.`;
}

// --- the map ---------------------------------------------------------------------

const MARK_KIND = Object.freeze({ fleet: 'Fleet', army: 'Army', air: 'Air group', front: 'Front', route: 'Convoy route' });
const MARK_SIDE = Object.freeze({ own: 'Yours', other: 'Foreign', none: 'Both sides' });
const CERTAINTY = Object.freeze({ known: 'Known', reported: 'Reported', stale: 'Stale' });

export const markKindWord = (k) => pick(MARK_KIND, k);
export const certaintyWord = (c) => pick(CERTAINTY, c);

/** "Foreign · Germany", or "Both sides" for a front. */
export function sideLine(mark) {
  const word = pick(MARK_SIDE, mark.side);
  return mark.side_name ? `${word} · ${mark.side_name}` : word;
}

/** "Smolensk", or "Leningrad – Smolensk – Odessa" for a line. */
export const whereLine = (mark) => mark.places.map((p) => p.name).join(' – ');

/** "Reported 1 October 1941 by Alfred Jodl": where it was then, not now. */
export const sightedLine = (mark) => `Reported ${worldDate(mark.reported)} by ${mark.source}`;

/** Lines first, then forces, by name: forces are drawn over the lines they sit on. */
export function sortMarks(marks) {
  const rank = (m) => (m.places.length > 1 ? 0 : 1);
  return [...marks].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

const MIN_SPAN = Object.freeze({ lon: 30, lat: 18 });

/** The box to draw, in degrees: what the marks (or, with none, the places) span,
 *  padded, never narrower than MIN_SPAN. 'world' is the whole map. */
export function mapBox(map, scope = 'reports') {
  if (scope === 'world') return { west: -180, east: 180, south: -60, north: 80 };
  const pts = (map.marks.length ? map.marks.flatMap((m) => m.places) : map.places);
  if (!pts.length) return mapBox(map, 'world');
  const lons = pts.map((p) => p.lon);
  const lats = pts.map((p) => p.lat);
  const grow = (lo, hi, min) => {
    const pad = Math.max((hi - lo) * 0.15, (min - (hi - lo)) / 2, 5);
    return [lo - pad, hi + pad];
  };
  const [west, east] = grow(Math.min(...lons), Math.max(...lons), MIN_SPAN.lon);
  const [south, north] = grow(Math.min(...lats), Math.max(...lats), MIN_SPAN.lat);
  return { west: Math.max(west, -180), east: Math.min(east, 180), south: Math.max(south, -85), north: Math.min(north, 85) };
}

/** An equirectangular projection for ``box``, squeezed by the cosine of its middle
 *  latitude so the high north is not drawn twice as wide as it is. */
export function projection(box) {
  const k = Math.cos(((box.north + box.south) / 2) * (Math.PI / 180));
  const x = (lon) => (lon - box.west) * k;
  const y = (lat) => box.north - lat;
  return { x, y, width: (box.east - box.west) * k, height: box.north - box.south };
}

const r1 = (n) => Math.round(n * 10) / 10;

/** One SVG path for the land rings that touch ``box`` (draw it even-odd: inner rings are lakes). */
export function landPath(rings, box, proj) {
  const out = [];
  for (const ring of rings) {
    let w = 180; let e = -180; let s = 90; let n = -90;
    for (const [lon, lat] of ring) {
      w = Math.min(w, lon); e = Math.max(e, lon); s = Math.min(s, lat); n = Math.max(n, lat);
    }
    if (e < box.west || w > box.east || n < box.south || s > box.north) continue;
    out.push(`M${ring.map(([lon, lat]) => `${r1(proj.x(lon))} ${r1(proj.y(lat))}`).join('L')}Z`);
  }
  return out.join('');
}
