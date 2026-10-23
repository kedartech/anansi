// The rules the interface never breaks (docs/web-design/README.md), as pure data.
// Every screen draws odds, visibility and standing through these, so one test
// file (test/rules.test.js) covers every screen.

/** Rule 4: odds are one of five bands, as words, never a number. */
export const BANDS = Object.freeze({
  impossible: { word: 'Impossible', pips: 0 },
  long_shot: { word: 'Long shot', pips: 1 },
  contested: { word: 'Contested', pips: 2 },
  likely: { word: 'Likely', pips: 3 },
  assured: { word: 'Assured', pips: 4 },
});

export function band(value) {
  const found = Object.hasOwn(BANDS, value) ? BANDS[value] : undefined;
  if (!found) throw new Error(`not an odds band: ${value}`);
  return found;
}

/** Rule 6: visibility always carries an icon and a word, never colour alone. */
export const VISIBILITY = Object.freeze({
  public: { word: 'Public', icon: 'public' },
  restricted: { word: 'Restricted', icon: 'restricted' },
  private: { word: 'Private', icon: 'private' },
  covert: { word: 'Covert', icon: 'covert' },
});

export function visibility(value) {
  const found = Object.hasOwn(VISIBILITY, value) ? VISIBILITY[value] : undefined;
  if (!found) throw new Error(`not a visibility: ${value}`);
  return found;
}

/** Orders take three of the four: restricted needs named seats, which the engine
 *  sets itself. */
export const ORDER_VISIBILITIES = Object.freeze(['private', 'public', 'covert']);

/** A relationship as the dossier words it. */
export function disposition(n) {
  if (n <= -3) return 'Hostile';
  if (n <= -1) return 'Cold';
  if (n === 0) return 'Neutral';
  if (n <= 2) return 'Warm';
  return 'Close';
}

export const GOAL_KINDS = Object.freeze({
  lasting: 'Lasting',
  undoable: 'Undoable',
});

/** Engine replies use a small slice of Discord markup: ``**bold**`` and ``- `` bullets.
 *  Parsed to plain tokens; the DOM layer turns them into text nodes, never HTML. */
export function parseReply(text) {
  const blocks = [];
  let list = null;
  for (const raw of String(text ?? '').split('\n')) {
    const bullet = raw.startsWith('- ');
    const line = bullet ? raw.slice(2) : raw;
    const spans = [];
    const parts = line.split('**');
    parts.forEach((part, i) => {
      if (part) spans.push({ text: part, strong: i % 2 === 1 && i < parts.length - 1 });
    });
    if (bullet) {
      if (!list) {
        list = { kind: 'list', items: [] };
        blocks.push(list);
      }
      list.items.push(spans);
    } else {
      list = null;
      blocks.push({ kind: raw.trim() ? 'line' : 'gap', spans });
    }
  }
  return blocks;
}

/** "14 h 22 m" to the next tick, from an ISO time. */
export function untilText(iso, now = Date.now()) {
  const ms = Date.parse(iso) - now;
  if (!Number.isFinite(ms)) return '';
  if (ms <= 0) return 'Due now';
  const mins = Math.round(ms / 60000);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h ? `${h} h ${m} m` : `${m} m`;
}

/** An in-world ISO date as "6 June 1944". */
export function worldDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December'];
  return `${d} ${months[m - 1]} ${y}`;
}
