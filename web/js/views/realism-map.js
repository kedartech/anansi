// The realism map: forces, fronts and routes as the seat's own papers place them,
// drawn over Natural Earth coastlines, and the same marks as a list for keyboard
// and screen-reader use. Takes view.realism.map (realism_api.py SeatMap).

import { h, svg } from '../dom.js';
import {
  certaintyWord, landPath, mapBox, markKindWord, projection, sideLine, sightedLine, sortMarks, whereLine,
} from '../realism-rules.js';

/** The marker for a force: shape says side, fill and line say certainty, a glyph says kind. */
function forceGlyph(mark, size) {
  const r = size / 2;
  const shape = mark.side === 'own'
    ? svg('rect', { x: -r * 1.4, y: -r, width: r * 2.8, height: r * 2, class: 'rl-mk-shape' })
    : svg('path', { d: `M0 ${-r * 1.4}L${r * 1.4} 0L0 ${r * 1.4}L${-r * 1.4} 0Z`, class: 'rl-mk-shape' });
  const q = r * 0.6;
  const inner = {
    fleet: svg('path', { d: `M${-q} 0H${q}M${-q} ${q / 2}Q0 ${q * 1.2} ${q} ${q / 2}`, class: 'rl-mk-glyph' }),
    army: svg('path', { d: `M${-q} ${-q / 1.5}L${q} ${q / 1.5}M${-q} ${q / 1.5}L${q} ${-q / 1.5}`, class: 'rl-mk-glyph' }),
    air: svg('path', { d: `M${-q} ${q / 2}L0 ${-q / 2}L${q} ${q / 2}`, class: 'rl-mk-glyph' }),
  }[mark.kind];
  const tick = mark.certainty === 'stale'
    ? svg('path', { d: `M${r * 1.2} ${-r * 1.6}l${r * 0.6} ${-r * 0.6}`, class: 'rl-mk-tick' })
    : null;
  return [shape, inner, tick];
}

function label(mark) {
  return `${mark.name}: ${markKindWord(mark.kind)}, ${sideLine(mark)}, ${certaintyWord(mark.certainty)}, ${whereLine(mark)}`;
}

/** The drawing: sea, land, the places the marks name, then lines under forces. */
function drawing(map, land, opts) {
  const box = mapBox(map, opts.scope);
  const proj = projection(box);
  const size = proj.width / 55;
  const named = new Set(map.marks.flatMap((m) => m.places.map((p) => p.name)));
  const inBox = (p) => p.lon >= box.west && p.lon <= box.east && p.lat >= box.south && p.lat <= box.north;
  const at = (p) => [proj.x(p.lon), proj.y(p.lat)];

  const places = map.places.filter(inBox).map((p) => {
    const [x, y] = at(p);
    return svg('g', { class: `rl-map-place${named.has(p.name) ? ' named' : ''}` },
      svg('circle', { cx: x, cy: y, r: size / 6 }),
      named.has(p.name) ? svg('text', { x: x + size / 3, y: y - size / 3, 'font-size': size * 0.7 }, p.name) : null);
  });

  const marks = sortMarks(map.marks).map((m) => {
    const select = () => opts.onSelect?.(m.name);
    const common = {
      class: `rl-mk rl-mk-${m.kind} rl-side-${m.side} rl-c-${m.certainty}${opts.selected === m.name ? ' selected' : ''}`,
      tabindex: 0,
      role: 'button',
      'aria-label': label(m),
      'aria-pressed': String(opts.selected === m.name),
      onclick: select,
      onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } },
    };
    if (m.places.length > 1) {
      const points = m.places.map((p) => at(p).join(',')).join(' ');
      return svg('g', common,
        svg('polyline', { points, class: 'rl-mk-hit' }),
        svg('polyline', { points, class: 'rl-mk-line' }),
        svg('title', {}, label(m)));
    }
    const [x, y] = at(m.places[0]);
    return svg('g', { ...common, transform: `translate(${x} ${y})` },
      opts.selected === m.name ? svg('circle', { r: size * 1.3, class: 'rl-mk-ring' }) : null,
      forceGlyph(m, size),
      svg('text', { x: size * 1.1, y: size * 1.4, 'font-size': size * 0.75, class: 'rl-mk-name' }, m.name),
      svg('title', {}, label(m)));
  });

  return svg('svg', {
    class: 'rl-map-svg', viewBox: `0 0 ${proj.width} ${proj.height}`, preserveAspectRatio: 'xMidYMid meet',
    role: 'group', 'aria-label': 'Map of what your papers report',
  },
  svg('rect', { x: 0, y: 0, width: proj.width, height: proj.height, class: 'rl-map-sea' }),
  land ? svg('path', { d: landPath(land.rings, box, proj), class: 'rl-map-land' }) : null,
  places,
  marks);
}

function legendSwatch(kind, side, certainty) {
  const mark = { kind, side, certainty };
  return svg('svg', { viewBox: '-14 -14 28 28', width: 22, height: 22, 'aria-hidden': 'true', focusable: 'false', class: `rl-mk rl-side-${side} rl-c-${certainty}` },
    forceGlyph(mark, 12));
}

function legendLine(kind) {
  return svg('svg', { viewBox: '0 0 28 12', width: 28, height: 12, 'aria-hidden': 'true', focusable: 'false', class: `rl-mk rl-mk-${kind} rl-c-reported` },
    svg('polyline', { points: '2,6 26,6', class: 'rl-mk-line' }));
}

function legend() {
  const item = (swatch, words) => h('li', {}, swatch, h('span', {}, words));
  return h('ul', { class: 'rl-legend', 'aria-label': 'Key' },
    item(legendSwatch('army', 'own', 'reported'), 'Yours'),
    item(legendSwatch('army', 'other', 'reported'), 'Foreign'),
    item(legendSwatch('fleet', 'own', 'known'), 'Known: your own side, lately reported'),
    item(legendSwatch('fleet', 'own', 'reported'), 'Reported: as a page has it, true or not'),
    item(legendSwatch('fleet', 'own', 'stale'), 'Stale: the latest page is over two weeks old'),
    item(legendLine('front'), 'Front'),
    item(legendLine('route'), 'Convoy route'));
}

function detail(mark, onOpen) {
  return h('section', { class: 'rl-map-detail', 'aria-labelledby': 'rl-map-detail-h' },
    h('h3', { id: 'rl-map-detail-h' }, mark.name),
    h('dl', {},
      h('dt', {}, 'What'), h('dd', {}, markKindWord(mark.kind)),
      h('dt', {}, 'Side'), h('dd', {}, sideLine(mark)),
      h('dt', {}, 'Certainty'), h('dd', {}, certaintyWord(mark.certainty)),
      h('dt', {}, 'Where'), h('dd', {}, whereLine(mark))),
    h('p', { class: 'muted' }, sightedLine(mark)),
    h('button', { type: 'button', class: 'rl-submit', onclick: () => onOpen?.({ id: mark.document }) }, 'Open the page'));
}

function table(map, onFind) {
  return h('table', { class: 'rl-map-list' },
    h('caption', {}, 'Every mark on your map, as your papers have it'),
    h('thead', {}, h('tr', {}, ['Name', 'What', 'Side', 'Certainty', 'Where', 'Source', ''].map((c) => h('th', { scope: 'col' }, c)))),
    h('tbody', {}, sortMarks(map.marks).map((m) => h('tr', { class: `rl-c-${m.certainty}` },
      h('th', { scope: 'row' }, m.name),
      h('td', {}, markKindWord(m.kind)),
      h('td', {}, sideLine(m)),
      h('td', {}, certaintyWord(m.certainty)),
      h('td', {}, whereLine(m)),
      h('td', {}, sightedLine(m)),
      h('td', {}, h('button', { type: 'button', class: 'rl-find', onclick: () => onFind(m.name) }, 'Find on map'))))));
}

/** `opts` is { mode: 'map'|'list', scope: 'reports'|'world', selected, onMode, onScope, onSelect(name, mode?), onOpen }. */
export function mapView(map, land, opts = {}) {
  const { mode = 'map', scope = 'reports' } = opts;
  const toggle = (value, word, current, on) => h('button', {
    type: 'button', class: 'rl-filter', 'aria-pressed': String(current === value), onclick: () => on?.(value),
  }, word);
  const bar = h('div', { class: 'rl-bar' },
    h('div', { class: 'rl-filters', role: 'group', 'aria-label': 'Show as' },
      toggle('map', 'Map', mode, opts.onMode), toggle('list', 'List', mode, opts.onMode)),
    mode === 'map'
      ? h('div', { class: 'rl-filters', role: 'group', 'aria-label': 'How much of the world' },
        toggle('reports', 'Your reports', scope, opts.onScope), toggle('world', 'The world', scope, opts.onScope))
      : null);
  const selected = map.marks.find((m) => m.name === opts.selected) ?? null;
  const empty = !map.marks.length
    ? h('p', { class: 'muted' }, 'No page has placed anything yet.')
    : null;
  const find = (name) => opts.onSelect?.(name, 'map');
  return h('section', { class: 'rl-map', 'aria-labelledby': 'rl-map-h' },
    h('h2', { id: 'rl-map-h' }, 'Map'),
    h('p', { class: 'muted' }, 'Drawn only from your own papers, as of when each was written.'),
    bar,
    empty,
    mode === 'list'
      ? (map.marks.length ? table(map, find) : null)
      : [h('figure', { class: 'rl-map-figure' }, drawing(map, land, { ...opts, scope })), legend(),
        selected ? detail(selected, opts.onOpen) : null]);
}
