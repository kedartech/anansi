// Realism views: the in-tray, a document page, the desk, the directive form, the situation
// board, the order tracker, the pace header and the lever form. Each takes the realism_api JSON and returns a node.

import { h, icon } from '../dom.js';
import {
  TRAY_FILTERS, addressLine, channelWord, directiveChannel, classificationWord, figureValue, filterInTray, gapLine, horizonWords, linesHeading, paceLine,
  powersSentence, reportLine, runLine, sortInTray, stepLine, trackerLine, trayCounts, trendLine,
} from '../realism-rules.js';
import { worldDate } from '../rules.js';

/** The classification stamp: the word, always. */
function stamp(c) {
  return h('span', { class: `stamp stamp-${c}` }, classificationWord(c).toUpperCase());
}

function list(items, cls) {
  return h('ul', { class: cls }, items.map((t) => h('li', {}, t)));
}

/** One folder in the tray: a tab with the author, a cover with the title, and a routing slip. */
function folder(d, onOpen) {
  const state = d.unread ? 'unread' : 'read';
  return h('li', { class: `rl-folder ${state}${d.flagged ? ' flagged' : ''}` },
    h('span', { class: 'rl-folder-tab' }, d.author),
    h('button', { type: 'button', class: 'rl-cover', onclick: () => onOpen?.(d) },
      d.unread ? h('span', { class: 'rl-ribbon' }, 'Unread') : null,
      d.flagged ? h('span', { class: 'rl-flagmark' }, h('span', { class: 'rl-clip', 'aria-hidden': 'true' }), 'Flagged') : null,
      h('span', { class: 'rl-doc-title' }, d.title),
      stamp(d.classification),
      h('dl', { class: 'rl-slip' },
        h('dt', {}, 'From'), h('dd', {}, d.author),
        h('dt', {}, 'Via'), h('dd', {}, channelWord(d.channel)),
        h('dt', {}, 'Arrived'), h('dd', {}, worldDate(d.arrives)))));
}

/** The folder grid under a filter bar. `opts` is { filter, sort, onFilter, onSort }. */
export function inTray(listing, onOpen, opts = {}) {
  const { filter = 'all', sort = 'newest', onFilter, onSort } = opts;
  const counts = trayCounts(listing.documents);
  const items = sortInTray(filterInTray(listing.documents, filter), sort);
  const tabs = TRAY_FILTERS.map(([key, word]) => h('button', {
    type: 'button', role: 'tab', id: `rl-filter-${key}`, 'aria-selected': String(filter === key),
    'aria-controls': 'rl-folders', tabindex: filter === key ? 0 : -1, class: 'rl-filter',
    onclick: () => onFilter?.(key),
    onkeydown: (e) => {
      const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const last = TRAY_FILTERS.length - 1;
      let to = TRAY_FILTERS.findIndex(([k]) => k === key) + step;
      if (to < 0) to = last;
      if (to > last) to = 0;
      onFilter?.(TRAY_FILTERS[to][0], true);
    },
  }, word, h('span', { class: 'rl-count' }, ` ${counts[key]}`)));
  return h('section', { class: 'rl-tray', 'aria-labelledby': 'rl-tray-h' },
    h('h2', { id: 'rl-tray-h' }, 'In-tray'),
    h('div', { class: 'rl-bar' },
      h('div', { role: 'tablist', 'aria-label': 'Show papers', class: 'rl-filters' }, tabs),
      h('label', { class: 'rl-sort' }, 'Sort ',
        h('select', { name: 'sort', onchange: (e) => onSort?.(e.target.value) },
          h('option', { value: 'newest', selected: sort === 'newest' }, 'Newest first'),
          h('option', { value: 'oldest', selected: sort === 'oldest' }, 'Oldest first')))),
    h('div', { role: 'tabpanel', id: 'rl-folders', 'aria-labelledby': `rl-filter-${filter}` },
      items.length
        ? h('ul', { class: 'rl-folders', role: 'list' }, items.map((d) => folder(d, onOpen)))
        : h('p', { class: 'rl-none' }, 'Nothing in this tray.')));
}

function figures(lines, { keepEmpty }) {
  const shown = keepEmpty ? lines : lines.filter((b) => b.value !== null);
  if (!shown.length) return null;
  return h('section', { class: 'rl-figures' },
    h('h3', {}, linesHeading(shown)),
    h('dl', { class: 'rl-body' }, shown.map((b) => [
      h('dt', {}, b.label),
      b.value === null
        ? h('dd', { class: 'rl-none' }, 'No figure given')
        : h('dd', {}, `${b.value}${b.unit ? ` ${b.unit}` : ''}`),
    ])));
}

export function documentPage(page) {
  const { item } = page;
  const paragraphs = page.prose ? page.prose.split(/\n{2,}/).filter((p) => p.trim()) : [];
  const nothing = !paragraphs.length && !page.body.length;
  return h('article', { class: 'rl-page', 'aria-labelledby': 'rl-page-h' },
    h('div', { class: 'rl-page-top' }, stamp(item.classification),
      h('span', { class: 'rl-page-via' }, `via ${channelWord(item.channel)}`)),
    h('h2', { id: 'rl-page-h' }, item.title),
    h('p', { class: 'rl-page-from' }, `From ${item.author}, issued ${worldDate(item.issued)}, arrived ${worldDate(item.arrives)}`),
    paragraphs.length ? h('div', { class: 'rl-prose' }, paragraphs.map((p) => h('p', {}, p))) : null,
    figures(page.body, { keepEmpty: !paragraphs.length }),
    nothing ? h('p', { class: 'rl-none' }, 'The text of this paper has not reached your desk.') : null);
}

/** An opened folder: the paper on the folder's inside, with Close folder and Flag/Unflag. */
export function dossier(page, { onClose, onFlag }) {
  const flagged = page.item.flagged;
  return h('section', { class: 'rl-dossier', 'aria-label': 'Open folder' },
    h('div', { class: 'rl-dossier-bar' },
      h('button', { type: 'button', class: 'rl-btn', onclick: () => onClose?.() }, 'Close folder'),
      h('button', {
        type: 'button', class: 'rl-btn', 'aria-pressed': String(flagged), onclick: () => onFlag?.(!flagged),
      }, flagged ? 'Unflag' : 'Flag')),
    documentPage(page));
}

function paragraph(text) {
  return text ? h('p', { class: 'rl-powers' }, text) : null;
}

/**
 * `opts` is { onSubmit, entities, directive }: the lever form's hand-off, the names entity
 * params offer, and the directive panel shown above the standard forms.
 */
export function desk(d, opts = {}) {
  const block = (title, items) => items.length
    ? h('section', { class: 'rl-desk-block' }, h('h3', {}, title), list(items))
    : null;
  return h('section', { class: 'rl-desk', 'aria-labelledby': 'rl-desk-h' },
    h('header', { class: 'rl-office' },
      h('h2', { id: 'rl-desk-h' }, d.office),
      d.principals.length ? h('p', {}, `Answerable to ${d.principals.join(', ')}`) : null,
      h('p', { class: 'rl-horizon' }, horizonWords(d.horizon_days))),
    opts.directive ?? null,
    d.powers.length ? h('section', { class: 'rl-desk-block' }, h('h3', {}, 'Powers'), paragraph(powersSentence(d.powers))) : null,
    block('Limits', d.constraints),
    block('Personal', d.personal),
    h('section', { class: 'rl-desk-block' }, h('h3', {}, 'Standard forms'),
      d.levers.length
        ? h('ul', { class: 'rl-instruments', role: 'list' }, d.levers.map((l) => instrument(l, opts)))
        : h('p', { class: 'rl-none' }, 'None at your disposal.')),
    h('section', { class: 'rl-desk-block' }, h('h3', {}, 'Your staff and contacts'),
      d.contacts.length
        ? h('ul', { class: 'rl-contacts', role: 'list' }, d.contacts.map((c) => h('li', {},
          CHANNEL_ICON[c.channel] ? [icon(CHANNEL_ICON[c.channel]), ' '] : null, h('strong', {}, c.person), ` (${channelWord(c.channel)})`)))
        : h('p', { class: 'rl-none' }, 'No one is within reach.')));
}

let directiveSeq = 0;

/**
 * Write a directive: to whom, how, and the words. `draft` is { to, channel, text } to
 * start from (kept across Revise); `onAsk` gets the same shape.
 */
export function directiveForm(contacts, draft, onAsk) {
  const id = `rl-dir-${directiveSeq++}`;
  const start = contacts.find((c) => c.person === draft?.to) ?? contacts[0];
  const how = h('select', { id: `${id}-how`, name: 'channel', required: true });
  const fill = (contact, chosen) => how.replaceChildren(...contact.channels.map((ch) => h('option', {
    value: ch, selected: ch === chosen,
  }, directiveChannel(ch))));
  const to = h('select', {
    id: `${id}-to`, name: 'to', required: true,
    onchange: () => fill(contacts.find((c) => c.person === to.value), null),
  }, contacts.map((c) => h('option', { value: c.person, selected: c === start }, c.person)));
  const words = h('textarea', { id: `${id}-text`, name: 'text', rows: 4, maxlength: 1000, required: true }, draft?.text ?? '');
  if (start) fill(start, draft?.channel);
  const form = h('form', {
    class: 'rl-form rl-directive', 'aria-label': 'Write a directive',
    onsubmit: (e) => { e.preventDefault(); onAsk?.({ to: to.value, channel: how.value, text: words.value }); },
  },
  h('div', { class: 'rl-field' }, h('label', { for: `${id}-to` }, 'To'), to),
  h('div', { class: 'rl-field' }, h('label', { for: `${id}-how` }, 'By'), how),
  h('div', { class: 'rl-field' }, h('label', { for: `${id}-text` }, 'Your instruction'), words),
  h('button', { type: 'submit', class: 'rl-submit' }, 'Prepare the order'));
  return h('section', { class: 'rl-desk-block', 'aria-labelledby': `${id}-h` },
    h('h3', { id: `${id}-h` }, 'Write a directive'),
    contacts.length ? form : h('p', { class: 'rl-none' }, 'No one is within reach.'));
}

/** The order the aide read a directive as, to sign or revise; or why there is none. */
export function proposalCard(p, { onSign, onRevise } = {}) {
  const revise = h('button', { type: 'button', class: 'rl-btn', onclick: () => onRevise?.(p) }, 'Revise');
  return h('section', { class: 'rl-desk-block rl-proposal', 'aria-labelledby': 'rl-proposal-h' },
    h('h3', { id: 'rl-proposal-h' }, p.lever ? 'Ready to sign' : 'This cannot be sent'),
    h('p', { class: 'rl-address' }, addressLine(p)),
    h('blockquote', { class: 'rl-words' }, p.text),
    p.lever
      ? [h('p', {}, 'Your aide reads this as: ', h('strong', {}, p.label)),
        p.terms.length ? list(p.terms, 'rl-terms') : null]
      : h('p', { class: 'rl-reason' }, p.reason),
    h('div', { class: 'rl-actions' },
      p.lever ? h('button', { type: 'button', class: 'rl-submit', onclick: () => onSign?.(p) }, 'Sign and send') : null,
      revise));
}

/** The design's icons that fit a channel; the others go without. */
const CHANNEL_ICON = Object.freeze({ in_person: 'restricted', cable: 'out', letter: 'mark', radio: 'public', press: 'public' });

let draftSeq = 0;

function instrument(lever, { onSubmit, entities = [] } = {}) {
  const id = `rl-draft-${draftSeq++}`;
  const panel = h('div', { id, hidden: true }, leverForm(lever, onSubmit, entities));
  const toggle = h('button', {
    type: 'button', class: 'rl-btn', 'aria-expanded': 'false', 'aria-controls': id,
    onclick: () => {
      const open = panel.hidden;
      panel.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
    },
  }, 'Draft');
  return h('li', { class: 'rl-instrument' },
    h('div', { class: 'rl-instrument-head' }, h('strong', {}, lever.label), toggle),
    lever.summary ? h('p', { class: 'rl-summary' }, lever.summary) : null,
    panel);
}

/** One source's line on a figure's card: the figure or the gap, the trend, who and when. */
function reading(unit, src, onOpen) {
  const shown = figureValue(src.value, '');
  return h('li', { class: 'rl-reading' },
    h('p', { class: 'rl-reading-head' },
      shown === null
        ? h('span', { class: 'rl-figure rl-gap' }, gapLine(src))
        : [h('span', { class: 'rl-figure' }, shown), unit ? h('span', { class: 'rl-unit' }, unit) : null,
          h('span', { class: 'rl-trend' }, trendLine(src))]),
    h('p', { class: 'rl-reported' }, reportLine(src)),
    src.history.length > 1 ? h('p', { class: 'rl-run' }, `Recent reports: ${runLine(src.history)}`) : null,
    h('button', { type: 'button', class: 'rl-btn', onclick: () => onOpen?.({ id: src.doc_id }) }, 'Open the report'));
}

/** The situation board: an index card per figure, each source on it as it gave the figure. */
export function board(s, { onOpen } = {}) {
  return h('section', { class: 'rl-board', 'aria-labelledby': 'rl-board-h' },
    h('h2', { id: 'rl-board-h' }, 'Situation'),
    h('p', { class: 'rl-board-note' }, 'The figures in the papers on your desk, each as its author gave it.'),
    s.figures.length
      ? h('ul', { class: 'rl-cards', role: 'list' }, s.figures.map((f) => h('li', { class: `rl-card${f.disagree ? ' differ' : ''}` },
        h('h3', {}, f.label),
        f.disagree ? h('p', { class: 'rl-differ' }, 'Sources differ') : null,
        h('ul', { class: 'rl-readings', role: 'list' }, f.sources.map((src) => reading(f.unit, src, onOpen))))))
      : h('p', { class: 'rl-none' }, 'No figures have reached your desk.'));
}

export function tracker(t) {
  return h('section', { class: 'rl-tracker', 'aria-labelledby': 'rl-track-h' },
    h('h2', { id: 'rl-track-h' }, 'Your orders'),
    t.orders.length
      ? h('ul', { class: 'rl-orders', role: 'list' }, t.orders.map((o) => h('li', { class: `rl-order status-${o.status}` },
        h('p', { class: 'rl-order-head' }, h('strong', {}, o.label), ` filed ${worldDate(o.filed)}`),
        o.terms.length ? list(o.terms, 'rl-terms') : null,
        h('p', { class: 'rl-order-line' },
          o.decision_needed ? icon('alert') : null, trackerLine(o)),
        h('ol', { class: 'rl-steps' }, o.steps.map((s) => h('li', {
          class: s.step === o.at_step && o.status === 'moving' ? 'rl-step here' : 'rl-step',
          'aria-current': s.step === o.at_step && o.status === 'moving' ? 'step' : null,
        }, stepLine(s)))))))
      : h('p', { class: 'rl-none' }, 'No orders filed.'));
}

export function paceHeader(clock) {
  return h('p', { class: 'rl-pace' }, icon('clock'), ' ', paceLine(clock));
}

let formSeq = 0;

/** A form built from lever params; `entities` are the names offered for entity params. */
export function leverForm(lever, onSubmit, entities = []) {
  const id = `rl-form-${formSeq++}`;
  const fields = lever.params.map((p, i) => {
    const fid = `${id}-${i}`;
    let input;
    if (p.kind === 'int') {
      input = h('input', { id: fid, name: p.name, type: 'number', step: 1, min: p.lo, max: p.hi, required: true });
    } else if (p.kind === 'enum' || p.kind === 'entity') {
      const names = (p.kind === 'enum' ? p.choices : entities.map((n) => ({ value: n, label: n })));
      input = h('select', { id: fid, name: p.name, required: true },
        h('option', { value: '' }, 'Choose…'), names.map((n) => h('option', { value: n.value }, n.label)));
    } else {
      input = h('textarea', { id: fid, name: p.name, rows: 3, required: true });
    }
    const hint = p.kind === 'int' && p.lo !== null && p.hi !== null ? ` (${p.lo} to ${p.hi})` : '';
    return h('div', { class: 'rl-field' }, h('label', { for: fid }, p.label, hint), input);
  });
  const form = h('form', {
    class: 'rl-form',
    'aria-label': lever.label,
    onsubmit: (e) => {
      e.preventDefault();
      const params = {};
      lever.params.forEach((p, i) => {
        const el = form.querySelector(`#${id}-${i}`);
        params[p.name] = p.kind === 'int' ? Number(el.value) : el.value;
      });
      onSubmit?.({ lever: lever.name, params });
    },
  }, fields, h('button', { type: 'submit', class: 'rl-submit' }, 'Sign and send'));
  return form;
}
