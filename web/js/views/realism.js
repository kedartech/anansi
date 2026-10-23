// Realism views: the in-tray, a document page, the desk, the order tracker, the
// pace header and the lever form. Each takes the realism_api JSON and returns a node.

import { h, icon } from '../dom.js';
import { channelWord, classificationWord, paceLine, sortInTray, trackerLine, unreadCount } from '../realism-rules.js';
import { worldDate } from '../rules.js';

/** The classification stamp: the word, always. */
function stamp(c) {
  return h('span', { class: `stamp stamp-${c}` }, classificationWord(c).toUpperCase());
}

function list(items, cls) {
  return h('ul', { class: cls }, items.map((t) => h('li', {}, t)));
}

export function inTray(listing, onOpen) {
  const items = sortInTray(listing.documents);
  const n = unreadCount(items);
  return h('section', { class: 'rl-tray', 'aria-labelledby': 'rl-tray-h' },
    h('h2', { id: 'rl-tray-h' }, 'In-tray ', h('span', { class: 'rl-count' }, `${n} unread`)),
    h('ul', { class: 'rl-docs', role: 'list' }, items.map((d) => h('li', { class: d.unread ? 'rl-doc unread' : 'rl-doc' },
      h('button', { type: 'button', class: 'rl-doc-open', onclick: () => onOpen?.(d) },
        h('span', { class: 'rl-doc-state' }, d.unread ? 'Unread' : 'Read'),
        h('span', { class: 'rl-doc-title' }, d.title),
        h('span', { class: 'rl-doc-meta' },
          `${d.author} · arrived ${worldDate(d.arrives)} · ${channelWord(d.channel)} `),
        stamp(d.classification))))));
}

export function documentPage(page) {
  const { item } = page;
  return h('article', { class: 'rl-page', 'aria-labelledby': 'rl-page-h' },
    h('div', { class: 'rl-page-top' }, stamp(item.classification),
      h('span', { class: 'rl-page-via' }, `via ${channelWord(item.channel)}`)),
    h('h2', { id: 'rl-page-h' }, item.title),
    h('p', { class: 'rl-page-from' }, `From ${item.author}, issued ${worldDate(item.issued)}, arrived ${worldDate(item.arrives)}`),
    page.body.length
      ? h('dl', { class: 'rl-body' }, page.body.map((b) => [
        h('dt', {}, b.label),
        h('dd', {}, b.value === null ? 'Not stated' : `${b.value}${b.unit ? ` ${b.unit}` : ''}`),
      ]))
      : null,
    page.prose
      ? h('div', { class: 'rl-prose' }, page.prose.split(/\n{2,}/).filter((p) => p.trim()).map((p) => h('p', {}, p)))
      : null);
}

export function desk(d) {
  const block = (title, items, empty) => h('section', { class: 'rl-desk-block' },
    h('h3', {}, title), items.length ? list(items) : h('p', { class: 'rl-none' }, empty));
  return h('section', { class: 'rl-desk', 'aria-labelledby': 'rl-desk-h' },
    h('h2', { id: 'rl-desk-h' }, d.office),
    h('p', { class: 'rl-horizon' }, `Planning horizon: ${d.horizon_days} days`),
    block('Powers', d.powers, 'None listed'),
    block('Levers', d.levers.map((l) => l.name), 'None'),
    block('Contacts', d.contacts.map((c) => `${c.person} (${channelWord(c.channel)})`), 'None'),
    block('Answerable to', d.principals, 'Nobody'),
    block('Constraints', d.constraints, 'None'),
    block('Personal', d.personal, 'Nothing to note'));
}

export function tracker(t) {
  return h('section', { class: 'rl-tracker', 'aria-labelledby': 'rl-track-h' },
    h('h2', { id: 'rl-track-h' }, 'Your orders'),
    t.orders.length
      ? h('ul', { class: 'rl-orders', role: 'list' }, t.orders.map((o) => h('li', { class: `rl-order status-${o.status}` },
        h('p', { class: 'rl-order-head' }, h('strong', {}, o.lever), ` filed ${worldDate(o.filed)}`),
        h('p', { class: 'rl-order-line' },
          o.decision_needed ? icon('alert') : null, trackerLine(o)),
        h('ol', { class: 'rl-steps' }, o.steps.map((s) => h('li', {
          class: s.step === o.at_step && o.status === 'moving' ? 'rl-step here' : 'rl-step',
          'aria-current': s.step === o.at_step && o.status === 'moving' ? 'step' : null,
        }, `${s.person}: ${s.action ?? 'pending'}${s.on ? `, ${worldDate(s.on)}` : ''}`))))))
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
      const names = p.kind === 'enum' ? p.choices : entities;
      input = h('select', { id: fid, name: p.name, required: true },
        h('option', { value: '' }, 'Choose…'), names.map((n) => h('option', { value: n }, n)));
    } else {
      input = h('textarea', { id: fid, name: p.name, rows: 3, required: true });
    }
    const hint = p.kind === 'int' && p.lo !== null && p.hi !== null ? ` (${p.lo} to ${p.hi})` : '';
    return h('div', { class: 'rl-field' }, h('label', { for: fid }, p.name, hint), input);
  });
  const form = h('form', {
    class: 'rl-form',
    'aria-label': lever.name,
    onsubmit: (e) => {
      e.preventDefault();
      const params = {};
      lever.params.forEach((p, i) => {
        const el = form.querySelector(`#${id}-${i}`);
        params[p.name] = p.kind === 'int' ? Number(el.value) : el.value;
      });
      onSubmit?.({ lever: lever.name, params });
    },
  }, h('h3', {}, lever.name), fields, h('button', { type: 'submit', class: 'rl-submit' }, 'File order'));
  return form;
}
