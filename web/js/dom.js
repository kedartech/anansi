// The only way the client makes DOM. Text always goes in as text nodes and
// attributes through setAttribute, so nothing from the server is ever parsed as
// HTML. There is no innerHTML anywhere in the client (test/rules.test.js checks).

import { band, parseReply, visibility } from './rules.js';

const SVG = 'http://www.w3.org/2000/svg';

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = Boolean(v);
    else if (k === 'disabled') el.disabled = Boolean(v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  append(el, children);
  return el;
}

/** An SVG element, the same way: attributes set one by one, text as text nodes. */
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

// --- icons (from the design's own SVGs) ------------------------------------------

const PATHS = {
  public: [['circle', { cx: 12, cy: 12, r: 2 }],
    ['path', { d: 'M8 8a5.7 5.7 0 0 0 0 8M16 8a5.7 5.7 0 0 1 0 8M5.2 5.2a9.6 9.6 0 0 0 0 13.6M18.8 5.2a9.6 9.6 0 0 1 0 13.6' }]],
  restricted: [['circle', { cx: 9, cy: 8, r: 3 }], ['path', { d: 'M3.5 19a5.5 5.5 0 0 1 11 0' }],
    ['circle', { cx: 17, cy: 9, r: 2.5 }], ['path', { d: 'M16 14.1a4.6 4.6 0 0 1 5 4.4' }]],
  private: [['rect', { x: 5, y: 11, width: 14, height: 9, rx: 1 }], ['path', { d: 'M8 11V8a4 4 0 0 1 8 0v3' }]],
  covert: [['path', { d: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z' }],
    ['circle', { cx: 12, cy: 12, r: 2.8 }], ['path', { d: 'M4 20 20 4' }]],
  mark: [['path', { d: 'M5 19 19 5M14 4l6 6M4 20l3-1-2-2z' }]],
  bug: [['rect', { x: 8, y: 6, width: 8, height: 13, rx: 4 }],
    ['path', { d: 'M12 6V3M4 10h4M16 10h4M4 16h4M16 16h4M9 3l1.5 3M15 3l-1.5 3' }]],
  check: [['path', { d: 'M5 12.5l4.5 4.5L19 7.5' }]],
  alert: [['path', { d: 'M12 3 2 20h20L12 3z' }], ['path', { d: 'M12 10v4M12 17v.5' }]],
  clock: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7v5l3 2' }]],
  out: [['path', { d: 'M14 4h6v16h-6M10 8l-4 4 4 4M6 12h10' }]],
};

export function icon(name, size = 13) {
  const svg = document.createElementNS(SVG, 'svg');
  for (const [k, v] of Object.entries({
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', 'stroke-width': 2, 'aria-hidden': 'true', focusable: 'false',
  })) svg.setAttribute(k, String(v));
  for (const [tag, attrs] of PATHS[name] ?? []) {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    svg.append(el);
  }
  return svg;
}

// --- the rule-bearing pieces -------------------------------------------------------

/** A visibility tag: icon plus word, always both (rule 6). */
export function visTag(value, { upper = false } = {}) {
  const v = visibility(value);
  return h('span', { class: `vis vis-${value}` }, icon(v.icon), upper ? v.word.toUpperCase() : v.word);
}

/** Odds as a band: pips plus the word, never a number (rule 4). */
export function oddsTag(value) {
  const b = band(value);
  const pips = h('span', { class: 'pips', 'aria-hidden': 'true' },
    [0, 1, 2, 3].map((i) => h('span', { class: i < b.pips ? 'on' : '' })));
  return h('span', { class: `odds odds-${value}` }, pips, h('span', {}, b.word));
}

/** Segments, for clocks, goal parts and meters. */
export function segments(filled, total, label) {
  return h('span', { class: 'segs', role: 'img', 'aria-label': label },
    Array.from({ length: total }, (_, i) => h('span', { class: i < filled ? 'on' : '' })));
}

/** An engine reply (plain lines, light markup) as DOM, without ever parsing HTML. */
export function reply(text) {
  const out = h('div', { class: 'reply' });
  for (const block of parseReply(text)) {
    const spans = (s) => s.map((x) => (x.strong ? h('strong', {}, x.text) : x.text));
    if (block.kind === 'list') out.append(h('ul', {}, block.items.map((it) => h('li', {}, spans(it)))));
    else if (block.kind === 'gap') out.append(h('div', { class: 'gap' }));
    else out.append(h('p', {}, spans(block.spans)));
  }
  return out;
}

/** Prose paragraphs, split on blank lines. */
export function prose(text, cls = 'prose') {
  return h('div', { class: cls },
    String(text ?? '').split(/\n{2,}/).filter((p) => p.trim()).map((p) => h('p', {}, p)));
}
