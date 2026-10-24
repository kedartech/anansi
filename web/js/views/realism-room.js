// The realism room: the in-tray, the situation board, the desk, the order tracker
// and the map for a seat in a realism game, under the pace header. Built from
// view.realism (realism_api.py).

import { api } from '../api.js';
import { h } from '../dom.js';
import { STEP_ACTIONS, entityChoices } from '../realism-rules.js';
import { worldDate } from '../rules.js';
import {
  board, desk, directiveForm, dossier, inTray, paceHeader, proposalCard, tracker,
} from './realism.js';
import { header } from './frame.js';
import { mapView } from './realism-map.js';

const TABS = [['tray', 'In-tray'], ['situation', 'Situation'], ['desk', 'Desk'], ['orders', 'Orders'], ['map', 'Map']];
const ui = {
  tab: 'tray', page: null, said: null, filter: 'all', sort: 'newest', draft: null, proposal: null,
  mapMode: 'map', mapScope: 'reports', mapSelected: null,
};

// The coastlines, fetched once and kept: the map draws without them until they come.
let land = null;
let landing = null;
function loadLand(then) {
  landing ??= fetch('/assets/land-110m.json')
    .then((r) => (r.ok ? r.json() : null))
    .then((json) => { land = json; })
    .catch(() => {});
  landing.then(then);
}

function stepForm(order, act) {
  const id = `rl-step-${order.id}`;
  const select = h('select', { id, name: 'action', required: true },
    STEP_ACTIONS.map(([value, label]) => h('option', { value }, label)));
  return h('form', {
    class: 'rl-form', 'aria-label': `Act on ${order.label}`,
    onsubmit: (e) => { e.preventDefault(); act({ order_id: order.id, action: select.value, params: {} }); },
  }, h('label', { for: id }, `Your decision on ${order.label}`), select,
  h('button', { type: 'submit', class: 'rl-submit' }, 'Take the step'));
}

export function realismRoom(ctx) {
  const { me, refresh } = ctx;
  const game = me.game;
  const rv = game.view.realism;
  const root = h('div', { class: 'screen war-screen realism-screen' });
  const status = h('p', { class: 'outcome', role: 'status', 'aria-live': 'polite' }, ui.said);
  const rerender = () => root.replaceChildren(...build());

  /** Post, show the reply, then re-read the game so the tray and tracker catch up. */
  async function run(action, body) {
    status.replaceChildren('Working…');
    try {
      const r = await api.realism(action, body);
      ui.said = r.reply;
      await refresh();
    } catch (ex) {
      status.replaceChildren(h('span', { class: 'form-error', role: 'alert' }, ex.message));
    }
  }

  /** The aide reads the directive; nothing is filed until the player signs. */
  async function ask(draft) {
    ui.draft = draft;
    status.replaceChildren('Your aide is reading it…');
    try {
      const r = await api.realism('directive', draft);
      ui.proposal = r.proposal ?? null;
      ui.said = ui.proposal ? null : r.reply; // the card says it
      rerender();
    } catch (ex) {
      status.replaceChildren(h('span', { class: 'form-error', role: 'alert' }, ex.message));
    }
  }

  /** File the signed order; a refusal leaves the reading on the desk to revise. */
  async function signed(p) {
    status.replaceChildren('Working…');
    try {
      const r = await api.realism('sign', { to: p.to, channel: p.channel, text: p.text, lever: p.lever, params: p.params });
      ui.said = r.reply;
      if (!r.refused) {
        ui.proposal = null;
        ui.draft = null;
      }
      await refresh();
    } catch (ex) {
      status.replaceChildren(h('span', { class: 'form-error', role: 'alert' }, ex.message));
    }
  }

  async function open(d) {
    try {
      ui.page = await api.realism('read', { doc_id: d.id });
      ui.tab = 'tray';
      await refresh();
    } catch (ex) {
      status.replaceChildren(h('span', { class: 'form-error', role: 'alert' }, ex.message));
    }
  }

  async function flag(on) {
    try {
      await api.realism('flag', { doc_id: ui.page.item.id, flagged: on });
      ui.page.item.flagged = on;
      await refresh();
    } catch (ex) {
      status.replaceChildren(h('span', { class: 'form-error', role: 'alert' }, ex.message));
    }
  }

  function body() {
    if (ui.tab === 'desk') {
      const directive = ui.proposal
        ? proposalCard(ui.proposal, { onSign: signed, onRevise: () => { ui.proposal = null; ui.said = null; rerender(); } })
        : directiveForm(rv.desk.contacts, ui.draft, ask);
      return h('div', { class: 'tab-body' },
        desk(rv.desk, { onSubmit: (v) => run('lever', v), entities: entityChoices(rv.desk), directive }));
    }
    if (ui.tab === 'situation') {
      return h('div', { class: 'tab-body' }, board(rv.situation, { onOpen: open }));
    }
    if (ui.tab === 'map' && rv.map) {
      if (!land) loadLand(() => { if (land && ui.tab === 'map') rerender(); });
      return h('div', { class: 'tab-body' }, mapView(rv.map, land, {
        mode: ui.mapMode,
        scope: ui.mapScope,
        selected: ui.mapSelected,
        onMode: (m) => { ui.mapMode = m; rerender(); },
        onScope: (sc) => { ui.mapScope = sc; rerender(); },
        onSelect: (name, mode) => {
          ui.mapSelected = ui.mapSelected === name && !mode ? null : name;
          if (mode) ui.mapMode = mode;
          rerender();
        },
        onOpen: open,
      }));
    }
    if (ui.tab === 'orders') {
      const waiting = rv.orders.orders.filter((o) => o.decision_needed);
      return h('div', { class: 'tab-body' }, tracker(rv.orders),
        waiting.map((o) => stepForm(o, (v) => run('step', v))));
    }
    if (ui.page) {
      return h('div', { class: 'tab-body' }, dossier(ui.page, { onClose: () => { ui.page = null; rerender(); }, onFlag: flag }));
    }
    const choose = (key, focus) => {
      ui.filter = key;
      rerender();
      if (focus) root.querySelector(`#rl-filter-${key}`)?.focus();
    };
    return h('div', { class: 'tab-body' }, inTray(rv.documents, open, {
      filter: ui.filter,
      sort: ui.sort,
      onFilter: choose,
      onSort: (value) => { ui.sort = value; rerender(); },
    }));
  }

  function build() {
    const advance = h('button', { type: 'button', class: 'primary', onclick: () => run('advance', {}) }, 'Advance to next wake');
    const extra = [
      h('span', { class: 'divider', 'aria-hidden': 'true' }),
      h('div', { class: 'stack' }, h('span', { class: 'title' }, worldDate(rv.clock.world_date)), paceHeader(rv.clock)),
      advance,
    ];
    const tablist = h('div', { role: 'tablist', 'aria-label': 'Workspace', class: 'tabs' },
      TABS.filter(([k]) => k !== 'map' || rv.map).map(([k, label]) => h('button', {
        type: 'button', role: 'tab', 'aria-selected': String(ui.tab === k), id: `rl-tab-${k}`,
        'aria-controls': 'rl-panel',
        onclick: () => { ui.tab = k; ui.said = null; rerender(); },
      }, label)));
    return [
      header({ ...ctx, game, role: 'Campaign', extra }),
      h('main', { class: 'col-centre' }, tablist,
        h('div', { role: 'tabpanel', id: 'rl-panel', 'aria-labelledby': `rl-tab-${ui.tab}`, class: 'tabpanel', tabindex: 0 }, body()),
        status),
    ];
  }

  root.append(...build());
  return root;
}
