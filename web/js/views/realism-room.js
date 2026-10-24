// The realism room: the in-tray, the desk and the order tracker for a seat in a
// realism game, under the pace header. Built from view.realism (realism_api.py).

import { api } from '../api.js';
import { h } from '../dom.js';
import { STEP_ACTIONS, entityChoices } from '../realism-rules.js';
import { worldDate } from '../rules.js';
import { desk, documentPage, inTray, leverForm, paceHeader, tracker } from './realism.js';
import { header } from './frame.js';

const TABS = [['tray', 'In-tray'], ['desk', 'Desk'], ['orders', 'Orders']];
const ui = { tab: 'tray', page: null, said: null };

function stepForm(order, act) {
  const id = `rl-step-${order.id}`;
  const select = h('select', { id, name: 'action', required: true },
    STEP_ACTIONS.map(([value, label]) => h('option', { value }, label)));
  return h('form', {
    class: 'rl-form', 'aria-label': `Act on ${order.lever}`,
    onsubmit: (e) => { e.preventDefault(); act({ order_id: order.id, action: select.value, params: {} }); },
  }, h('label', { for: id }, `Your decision on ${order.lever}`), select,
  h('button', { type: 'submit', class: 'rl-submit' }, 'Take the step'));
}

export function realismRoom(ctx) {
  const { me, refresh } = ctx;
  const game = me.game;
  const rv = game.view.realism;
  const root = h('div', { class: 'screen war-screen' });
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

  async function open(d) {
    try {
      ui.page = await api.realism('read', { doc_id: d.id });
      await refresh();
    } catch (ex) {
      status.replaceChildren(h('span', { class: 'form-error', role: 'alert' }, ex.message));
    }
  }

  function body() {
    if (ui.tab === 'desk') {
      const names = entityChoices(rv.desk);
      return h('div', { class: 'tab-body' }, desk(rv.desk),
        rv.desk.levers.map((l) => leverForm(l, (v) => run('lever', v), names)));
    }
    if (ui.tab === 'orders') {
      const waiting = rv.orders.orders.filter((o) => o.decision_needed);
      return h('div', { class: 'tab-body' }, tracker(rv.orders),
        waiting.map((o) => stepForm(o, (v) => run('step', v))));
    }
    return h('div', { class: 'tab-body' }, inTray(rv.documents, open),
      h('div', { class: 'rl-reader', 'aria-label': 'Reader' },
        ui.page ? documentPage(ui.page) : h('p', { class: 'rl-none' }, 'Open a document to read it here.')));
  }

  function build() {
    const advance = h('button', { type: 'button', class: 'primary', onclick: () => run('advance', {}) }, 'Advance to next wake');
    const extra = [
      h('span', { class: 'divider', 'aria-hidden': 'true' }),
      h('div', { class: 'stack' }, h('span', { class: 'title' }, worldDate(rv.clock.world_date)), paceHeader(rv.clock)),
      advance,
    ];
    const tablist = h('div', { role: 'tablist', 'aria-label': 'Workspace', class: 'tabs' },
      TABS.map(([k, label]) => h('button', {
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
