// The realism room: one surface for a seat in a realism game. Papers on the left (the
// in-tray, the situation board and the map, with an opened paper read in place), the
// desk in the centre, and the aide and the pending orders on the right, under the pace
// header. Built from view.realism (realism_api.py). Layout: docs/web-design/realism-room-unified.md.

import { api } from '../api.js';
import { h, icon } from '../dom.js';
import { STEP_ACTIONS, entityChoices } from '../realism-rules.js';
import { worldDate } from '../rules.js';
import {
  aidePanel, board, desk, directiveForm, dossier, inTray, paceHeader, pendingOrders, proposalCard,
} from './realism.js';
import { header } from './frame.js';
import { mapView } from './realism-map.js';

const PANELS = [['tray', 'In-tray'], ['situation', 'Situation'], ['map', 'Map']];
const ui = {
  panel: 'tray', wide: false, page: null, said: null, error: null, busy: null,
  filter: 'all', sort: 'newest', draft: null, proposal: null, brief: null,
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
    class: 'rl-form rl-step-form', 'aria-label': `Act on ${order.label}`,
    onsubmit: (e) => { e.preventDefault(); act({ order_id: order.id, action: select.value, params: {} }); },
  }, h('label', { for: id }, 'Your decision'), select,
  h('button', { type: 'submit', class: 'rl-submit' }, 'Take the step'));
}

/** The game menu: pause, resume and end for the host; how to start another game. */
function gameMenu(game, ctx, work) {
  const out = h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
  const dialog = h('dialog', { class: 'sheet rl-menu', 'aria-labelledby': 'rl-menu-h' });
  const close = () => dialog.close();
  const call = async (action) => {
    out.replaceChildren('Working…');
    try {
      const r = await api.game(action);
      if (r.refused) { out.replaceChildren(r.reply); return; }
      ui.said = r.reply;
      close();
      if (action === 'end') ctx.go('new');
      else await ctx.refresh();
    } catch (ex) {
      out.replaceChildren(h('span', { class: 'form-error', role: 'alert' }, ex.message));
    }
  };
  const confirmEnd = h('div', { class: 'rl-confirm', hidden: true },
    h('p', {}, h('strong', {}, `End ${game.name} for everyone?`),
      ' No one can play it again and its invite codes stop working. Its record is kept.'),
    h('div', { class: 'row' },
      h('button', { type: 'button', class: 'ghost', onclick: () => { confirmEnd.hidden = true; endBtn.hidden = false; } }, 'Keep playing'),
      h('button', { type: 'button', class: 'danger', onclick: () => call('end') }, 'End the game')));
  const endBtn = h('button', {
    type: 'button', class: 'ghost', onclick: () => { endBtn.hidden = true; confirmEnd.hidden = false; },
  }, 'End game…');
  const host = game.host
    ? [
      h('h3', {}, 'You are the host'),
      h('p', { class: 'hint' }, game.paused
        ? 'The game is paused: the calendar does not move on its own.'
        : 'The game is running.'),
      h('div', { class: 'row' },
        game.paused
          ? h('button', { type: 'button', class: 'ghost', onclick: () => call('resume') }, 'Resume')
          : h('button', { type: 'button', class: 'ghost', onclick: () => call('pause') }, 'Pause'),
        endBtn),
      confirmEnd,
    ]
    : [h('p', { class: 'hint' }, 'Only the host can pause or end this game.')];
  dialog.append(h('div', { class: 'rl-menu-body' },
    h('h2', { id: 'rl-menu-h' }, game.name),
    host,
    h('h3', {}, 'A new game'),
    h('p', { class: 'hint' }, game.host
      ? 'You play one game at a time. End this one, and you can start another straight away.'
      : 'You play one game at a time. When this one ends you can start your own.'),
    out,
    h('div', { class: 'row end' }, h('button', { type: 'button', class: 'ghost', onclick: close }, 'Close'))));
  const open = h('button', {
    type: 'button', class: 'rl-menu-btn', 'aria-haspopup': 'dialog', onclick: () => { work.idle() && dialog.showModal(); },
  }, 'Game', icon('chevron', 12));
  return [open, dialog];
}

export function realismRoom(ctx) {
  const { me, refresh } = ctx;
  const game = me.game;
  const rv = game.view.realism;
  const seatName = game.view.seat?.name ?? '';
  const root = h('div', { class: 'screen war-screen realism-screen' });
  const rerender = () => root.replaceChildren(...build());

  /**
   * Run one slow call: the status line says what is happening in plain words, every
   * control is held until it is done, and the room is re-read after.
   */
  async function work(message, fn) {
    if (ui.busy) return;
    ui.busy = message;
    ui.error = null;
    const clicked = document.activeElement;
    root.setAttribute('aria-busy', 'true');
    if (clicked?.tagName === 'BUTTON') clicked.classList.add('rl-working');
    for (const el of root.querySelectorAll('button, select, textarea, input')) el.disabled = true;
    showStatus();
    try {
      await fn();
    } catch (ex) {
      ui.error = ex.message;
    } finally {
      ui.busy = null;
    }
    await refresh();
  }
  work.idle = () => !ui.busy;

  let status = null;
  function showStatus() {
    if (!status) return;
    if (ui.busy) {
      status.className = 'rl-status busy';
      status.replaceChildren(h('span', { class: 'rl-spinner', 'aria-hidden': 'true' }), ui.busy);
    } else if (ui.error) {
      status.className = 'rl-status error';
      status.replaceChildren(h('span', { role: 'alert' }, ui.error));
    } else {
      status.className = ui.said ? 'rl-status said' : 'rl-status';
      status.replaceChildren(ui.said ?? '');
    }
  }

  const act = (message, action, body, then) => work(message, async () => {
    const r = await api.realism(action, body);
    ui.said = r.reply;
    then?.(r);
  });

  /** The aide reads the directive; nothing is filed until the player signs. */
  const ask = (draft) => {
    ui.draft = draft;
    return act('Your aide is reading it…', 'directive', draft, (r) => {
      ui.proposal = r.proposal ?? null;
      if (ui.proposal) ui.said = null; // the card says it
    });
  };

  /** File the signed order; a refusal leaves the reading on the desk to revise. */
  const signed = (p) => act(`Sending it to ${p.to}…`, 'sign',
    { to: p.to, channel: p.channel, text: p.text, lever: p.lever, params: p.params },
    (r) => { if (!r.refused) { ui.proposal = null; ui.draft = null; } });

  const open = (d) => work('Fetching the paper…', async () => {
    ui.page = await api.realism('read', { doc_id: d.id });
    ui.pageFrom = ui.panel;
    ui.panel = 'tray';
    ui.said = null;
  });

  const flag = (on) => work(on ? 'Flagging it…' : 'Unflagging it…', async () => {
    await api.realism('flag', { doc_id: ui.page.item.id, flagged: on });
    ui.page.item.flagged = on;
  });

  function choosePanel(key) {
    ui.panel = key;
    ui.page = null;
    rerender();
    root.querySelector(`#rl-panel-${key}`)?.focus();
  }

  function papers() {
    const unread = rv.documents.unread ?? rv.documents.documents.filter((d) => d.unread).length;
    const switcher = h('div', { role: 'tablist', 'aria-label': 'Papers', class: 'rl-switch' },
      PANELS.map(([k, label]) => h('button', {
        type: 'button', role: 'tab', id: `rl-panel-${k}`, 'aria-selected': String(ui.panel === k && !ui.page),
        'aria-controls': 'rl-papers-body', tabindex: ui.panel === k ? 0 : -1,
        onclick: () => choosePanel(k),
        onkeydown: (e) => {
          const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
          if (!step) return;
          e.preventDefault();
          const at = PANELS.findIndex(([x]) => x === k);
          choosePanel(PANELS[(at + step + PANELS.length) % PANELS.length][0]);
        },
      }, label, k === 'tray' && unread ? h('span', { class: 'rl-count' }, ` ${unread}`) : null)));
    const widen = h('button', {
      type: 'button', class: 'rl-btn rl-widen', 'aria-pressed': String(ui.wide),
      onclick: () => { ui.wide = !ui.wide; rerender(); },
    }, ui.wide ? 'Narrow' : 'Widen');
    return h('section', { class: 'rl-col rl-papers', 'aria-label': 'Papers' },
      h('div', { class: 'rl-col-head' }, switcher, widen),
      h('div', { id: 'rl-papers-body', role: 'tabpanel', 'aria-labelledby': `rl-panel-${ui.panel}`, class: 'rl-col-body' },
        papersBody()));
  }

  function papersBody() {
    if (ui.page) {
      return dossier(ui.page, {
        onClose: () => { ui.page = null; ui.panel = ui.pageFrom ?? 'tray'; rerender(); },
        onFlag: flag,
      });
    }
    if (ui.panel === 'situation') return board(rv.situation, { onOpen: open });
    if (ui.panel === 'map') {
      if (!rv.map) {
        return h('section', { class: 'rl-map', 'aria-labelledby': 'rl-map-h' },
          h('h2', { id: 'rl-map-h' }, 'Map'),
          h('p', { class: 'rl-none' }, 'This game has no map: its places were set down without positions, so there is nothing to draw. Games started now carry one.'));
      }
      if (!land) loadLand(() => { if (land && ui.panel === 'map') rerender(); });
      return mapView(rv.map, land, {
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
      });
    }
    const choose = (key, focus) => {
      ui.filter = key;
      rerender();
      if (focus) root.querySelector(`#rl-filter-${key}`)?.focus();
    };
    return inTray(rv.documents, open, {
      filter: ui.filter,
      sort: ui.sort,
      onFilter: choose,
      onSort: (value) => { ui.sort = value; rerender(); },
    });
  }

  function deskColumn() {
    const directive = ui.proposal
      ? proposalCard(ui.proposal, { onSign: signed, onRevise: () => { ui.proposal = null; ui.said = null; rerender(); } })
      : directiveForm(rv.desk.contacts, ui.draft, ask);
    return h('section', { class: 'rl-col rl-desk-col', 'aria-label': 'Desk' },
      h('div', { class: 'rl-col-body' },
        desk(rv.desk, {
          onSubmit: (v) => act('Sending it…', 'lever', v),
          entities: entityChoices(rv.desk),
          directive,
        })));
  }

  /** Open a standard form on the desk, for a draft the aide suggested. */
  function useDraft(d) {
    const item = root.querySelector(`.rl-instrument[data-lever="${CSS.escape(d.lever)}"]`);
    if (!item) return;
    const toggle = item.querySelector('[aria-expanded]');
    if (toggle?.getAttribute('aria-expanded') === 'false') toggle.click();
    item.scrollIntoView({ block: 'center' });
    item.querySelector('input, select, textarea')?.focus();
  }

  function sideColumn() {
    return h('aside', { class: 'rl-col rl-side', 'aria-label': 'Pending orders and your aide' },
      h('div', { class: 'rl-col-body' },
        pendingOrders(rv.orders, (o) => stepForm(o, (v) => act('Recording your decision…', 'step', v))),
        aidePanel(ui.brief, rv.desk.contacts, rv.counsel ?? [], {
          brief: () => act('Your aide is reading your papers…', 'aide', {}, (r) => { ui.brief = r.brief ?? null; }),
          consult: (to, question) => act(`Waiting for ${to} to answer…`, 'consult', { to, question }),
          useDraft,
        })));
  }

  function build() {
    const advance = h('button', {
      type: 'button', class: 'primary',
      onclick: () => act('The wake is running. New papers are being written; this can take a minute…', 'advance', {},
        () => { ui.brief = null; }),
    }, 'Advance to next wake');
    const [menuBtn, menu] = gameMenu(game, ctx, work);
    const extra = [
      h('span', { class: 'divider', 'aria-hidden': 'true' }),
      h('div', { class: 'stack rl-clock' }, h('span', { class: 'title' }, worldDate(rv.clock.world_date)), paceHeader(rv.clock)),
      advance,
      menuBtn,
    ];
    status = h('div', { class: 'rl-status', role: 'status', 'aria-live': 'polite' });
    showStatus();
    return [
      header({ ...ctx, game, role: 'Campaign', extra, identity: { name: seatName, office: rv.desk.office } }),
      status,
      h('main', { class: `rl-room${ui.wide ? ' wide' : ''}` }, papers(), deskColumn(), sideColumn()),
      menu,
    ];
  }

  root.append(...build());
  return root;
}
