// The war room (design D1-D6): position on the left, briefing and dossier in the
// centre with the order box, the agent and the Chronicle on the right. Map and
// ledger are designed but the engine has nothing to feed them yet, so their tabs
// say so rather than show invented figures.

import { api } from '../api.js';
import { clear, h, icon, oddsTag, prose, reply, segments, visTag } from '../dom.js';
import { GOAL_KINDS, ORDER_VISIBILITIES, disposition, untilText, worldDate } from '../rules.js';
import { header } from './frame.js';

const ui = { tab: 'briefing', tick: null, asked: [] };

function contestBand(contests) {
  if (!contests.length) return null;
  return h('section', { class: 'contests', 'aria-label': 'Contests' },
    contests.map((c) => {
      const rivalry = c.kind === 'rivalry';
      const lead = rivalry && c.sides.length >= 2
        ? (c.track > 0 ? c.sides[0] : c.track < 0 ? c.sides[1] : null) : null;
      const pct = rivalry ? (c.track + 100) / 2 : c.track;
      const where = c.decided
        ? (c.winner ? `Decided · ${c.winner}` : 'Decided')
        : `${c.countdown_days} days to the deciding point`;
      return h('div', { class: 'contest' },
        h('div', { class: 'contest-head' },
          h('span', { class: 'contest-name' }, c.name),
          h('span', { class: 'num' }, rivalry ? (c.track > 0 ? `+${c.track}` : String(c.track)) : `${c.track} of 100`),
          lead ? h('span', { class: 'muted' }, `favours ${lead}`) : null,
          h('span', { class: 'muted small' }, icon('clock', 11), where),
          visTag(c.visibility)),
        h('div', { class: 'track-row' },
          h('span', { class: 'side', title: rivalry ? (c.sides[1] ?? '') : '' }, rivalry ? (c.sides[1] ?? '') : ''),
          h('div', {
            class: 'track', role: 'img',
            'aria-label': rivalry
              ? `Track at ${c.track} of minus 100 to plus 100${lead ? `, favours ${lead}` : ''}`
              : `Track at ${c.track} of 100`,
          }, h('span', { class: 'mid', 'aria-hidden': 'true' }),
          h('span', { class: 'marker', style: null, 'data-pct': String(pct) })),
          h('span', { class: 'side', title: c.sides[0] ?? '' }, c.sides[0] ?? '')));
    }));
}

function position(view) {
  const s = view.seat;
  const pending = view.orders.filter((o) => o.pending);
  const slotLabel = `${pending.length} of ${s.slots} slots in use`;
  const ops = h('section', {},
    h('div', { class: 'sec-head' }, h('h2', {}, 'Operations'),
      segments(pending.length, s.slots, slotLabel), h('span', { class: 'muted small' }, slotLabel)),
    pending.length
      ? pending.map((o) => h('article', { class: 'op' },
        h('div', { class: 'op-head' }, h('span', { class: 'verb' }, o.verb.toUpperCase()), h('span', { class: 'muted small' }, `Filed tick ${o.tick}`)),
        h('span', { class: 'op-text' }, o.text || o.verb),
        h('div', { class: 'row' }, visTag(o.visibility), o.odds ? oddsTag(o.odds) : h('span', { class: 'muted small' }, 'Odds appear once the tick freezes'))))
      : h('p', { class: 'muted' }, 'Nothing pending. A new order starts at once while a slot is free.'));

  const means = h('section', {}, h('h2', {}, 'Means'),
    view.holdings.length
      ? h('ul', { class: 'means' }, view.holdings.map((a) => h('li', {},
        h('span', { class: 'mean-name' }, a.name),
        h('span', { class: 'muted small' }, a.kind, a.where ? ` · ${a.where}` : ''),
        segments(a.tier, 5, `Tier ${a.tier} of 5`),
        h('span', { class: 'small' }, `Tier ${a.tier}`),
        a.degraded ? h('span', { class: 'caution small' }, icon('alert', 11), 'Degraded') : null,
        visTag(a.visibility))))
      : h('p', { class: 'muted' }, 'Nothing held.'));

  const standing = view.standing.length ? h('section', {}, h('h2', {}, 'Standing'),
    view.standing.map((m) => h('div', { class: 'meter' },
      h('div', { class: 'row between' }, h('span', {}, m.name), h('span', { class: 'num' }, `${m.value} of ${m.hi}`)),
      h('div', { class: 'bar', role: 'img', 'aria-label': `${m.name} ${m.value} of ${m.hi}` },
        h('span', { 'data-pct': String(Math.round(((m.value - m.lo) / Math.max(1, m.hi - m.lo)) * 100)) }))))) : null;

  const g = s.goal;
  const goal = h('section', {},
    h('div', { class: 'sec-head' }, h('h2', {}, 'Your secret goal'), h('span', { class: 'vis vis-private' }, icon('private', 11), 'Owner only')),
    g ? [h('p', { class: 'goal-text' }, g.text),
      h('div', { class: 'row' }, segments(g.parts_met, g.parts, `${g.parts_met} of ${g.parts} parts met`),
        h('span', { class: 'small' }, `${g.parts_met} of ${g.parts} parts met`),
        h('span', { class: 'tag' }, GOAL_KINDS[g.kind] ?? g.kind),
        g.holding ? h('span', { class: 'settled small' }, icon('check', 11), 'Holding') : null)]
      : h('p', { class: 'muted' }, 'You play without a secret goal.'));

  const rivals = h('section', {}, h('h2', {}, "Rival goals · what you've uncovered"),
    view.rivals.length
      ? view.rivals.map((r) => h('div', { class: 'rival' },
        h('span', { class: 'rival-name' }, r.seat), h('span', { class: 'muted small' }, `${r.pieces.length} of 4 pieces`),
        h('ol', {}, r.pieces.map((p) => h('li', {}, p)))))
      : h('p', { class: 'muted' }, 'Nothing uncovered yet. Spying on a rival reveals their goal one piece at a time.'));

  return h('aside', { class: 'col-left', 'aria-label': 'Your position', tabindex: 0 }, ops, means, standing, goal, rivals);
}

function briefingTab(view, game) {
  const all = view.dispatches;
  const pick = all.find((d) => d.tick === ui.tick) ?? all[0];
  const agentName = view.seat.agent?.name ?? 'Your agent';
  if (!pick) {
    return h('div', { class: 'tab-body' },
      h('article', { class: 'paper', 'aria-label': 'Briefing' },
        h('div', { class: 'paper-head' },
          h('div', {}, h('span', { class: 'eyebrow' }, agentName), h('h1', {}, `Briefing for ${view.seat.name}`)),
          visTag('private', { upper: true })),
        h('p', { class: 'dispatch' }, 'No briefing yet. One arrives after each tick.')),
      view.brief ? h('article', { class: 'paper', 'aria-label': 'Historical brief' },
        h('div', { class: 'paper-head' }, h('span', { class: 'eyebrow' }, 'Historical brief · tick 0'), visTag('private', { upper: true })),
        prose(view.brief, 'prose dispatch')) : null);
  }
  const nav = h('nav', { class: 'ticks', 'aria-label': 'Earlier briefings' },
    h('span', { class: 'muted small' }, 'Briefings by tick'),
    all.slice(0, 12).map((d, i) => d === pick
      ? h('span', { 'aria-current': 'page' }, i === 0 ? `${d.tick} · latest` : String(d.tick))
      : h('a', { href: '#', onclick: (e) => { e.preventDefault(); ui.tick = d.tick; rerender(); } }, String(d.tick))),
    view.brief ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); ui.tick = 0; rerender(); } }, 'Brief') : null);
  const text = ui.tick === 0 && view.brief ? view.brief : pick.text;
  const label = ui.tick === 0 && view.brief ? 'Historical brief · tick 0' : `Tick ${pick.tick}`;
  return h('div', { class: 'tab-body' },
    h('article', { class: 'paper', 'aria-label': `Briefing, ${label}` },
      h('div', { class: 'paper-head' },
        h('div', {},
          h('span', { class: 'eyebrow' }, `${agentName} · ${view.seat.agent?.role ?? ''}`),
          h('h1', {}, `Briefing for ${view.seat.name}`),
          h('span', { class: 'muted small' }, label, game.date ? ` · ${worldDate(game.date)}` : '')),
        visTag('private', { upper: true })),
      nav,
      prose(text, 'prose dispatch')));
}

function dossierTab(view) {
  const rel = view.relationships;
  const grouped = {};
  for (const f of view.facts) (grouped[f.visibility] ??= []).push(f);
  return h('div', { class: 'tab-body' },
    h('article', { class: 'paper', 'aria-label': 'Dossier' },
      h('div', { class: 'paper-head' }, h('div', {}, h('span', { class: 'eyebrow' }, 'Dossier'), h('h1', {}, view.seat.name)), visTag('private', { upper: true })),
      h('p', { class: 'muted small' }, 'Everything here is what your seat has been told. Some of it may be false; nothing marks which.'),
      ['public', 'restricted', 'private', 'covert'].filter((k) => grouped[k]?.length).map((k) => h('section', {},
        h('h2', {}, visTag(k)),
        h('ul', { class: 'facts' }, grouped[k].map((f) => h('li', {},
          h('span', {}, `${f.subject} `, h('em', {}, f.predicate.replaceAll('_', ' ')), ` ${f.object}`),
          h('span', { class: 'muted small' }, ` · tick ${f.tick}`)))))),
      rel.length ? h('section', {}, h('h2', {}, 'Relationships'),
        h('ul', { class: 'facts' }, rel.map((r) => h('li', {}, h('span', {}, r.with), ' · ',
          h('strong', {}, disposition(r.disposition)), ' ', visTag(r.visibility))))) : null,
      view.clocks.length ? h('section', {}, h('h2', {}, 'Open clocks'),
        h('ul', { class: 'facts' }, view.clocks.map((c) => h('li', {},
          h('span', {}, c.name), ' ', segments(c.filled, c.total, `${c.filled} of ${c.total}`),
          h('span', { class: 'small' }, ` ${c.filled} of ${c.total}`), ' ', visTag(c.visibility))))) : null,
      view.powers.length ? h('section', {}, h('h2', {}, 'Powers'),
        h('ul', { class: 'facts' }, view.powers.map((p) => h('li', {}, p.name,
          p.played_by ? h('span', { class: 'muted' }, ` · played by ${p.played_by}`) : null)))) : null));
}

function notLive(what) {
  return h('div', { class: 'tab-body' }, h('div', { class: 'paper empty' },
    h('h2', {}, `${what} is not live yet`),
    h('p', {}, `The design has a ${what.toLowerCase()} here. The engine has no data source for it yet, so nothing is shown rather than invented figures.`)));
}

function orderBox(view, game, refresh) {
  const agentName = view.seat.agent?.name ?? 'your agent';
  const out = h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
  const text = h('textarea', { id: 'wr-order', rows: 2, maxlength: 4000, placeholder: `Write it in your own words. ${agentName} turns it into an order.` });
  const spin = h('input', { id: 'wr-spin', type: 'text', maxlength: 280, placeholder: "What you'll say in public about it" });
  const spinCount = h('span', { class: 'muted small num' }, '0 / 280');
  spin.addEventListener('input', () => { spinCount.textContent = `${spin.value.length} / 280`; });
  let vis = 'private';
  const radios = ORDER_VISIBILITIES.map((v) => {
    const b = h('button', { type: 'button', role: 'radio', 'aria-checked': String(v === vis), class: 'seg' }, visTag(v));
    b.addEventListener('click', () => {
      vis = v;
      for (const r of radios) r.setAttribute('aria-checked', String(r === b));
    });
    return b;
  });
  const file = h('button', { type: 'submit', class: 'primary' }, 'File order');
  const form = h('form', { class: 'order-box', 'aria-label': 'New order' },
    h('div', { class: 'row between' }, h('label', { for: 'wr-order' }, `Order to ${agentName}`),
      h('span', { class: 'muted small' }, 'Odds appear once the tick freezes, as a band.')),
    text,
    h('div', { class: 'order-row' },
      h('fieldset', {}, h('legend', {}, 'Visibility'), h('div', { role: 'radiogroup', 'aria-label': 'Visibility', class: 'segs-row' }, radios)),
      h('div', { class: 'grow' },
        h('div', { class: 'row between' }, h('label', { for: 'wr-spin' }, 'Public spin line ', h('span', { class: 'muted' }, '(optional)')), spinCount),
        spin),
      file),
    out);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!text.value.trim()) return;
    file.disabled = true;
    out.replaceChildren(h('p', { class: 'muted' }, `${agentName} is drafting the order. This usually takes 10 to 30 seconds.`));
    try {
      const r = await api.seat('order', { text: text.value, visibility: vis, spin: spin.value });
      out.replaceChildren(reply(r.reply));
      if (!r.refused && !/\?\s*$/.test(r.reply)) {
        text.value = '';
        spin.value = '';
        await refresh({ keepOutcome: true });
      }
    } catch (ex) {
      out.replaceChildren(h('p', { class: 'form-error', role: 'alert' }, ex.message));
    } finally {
      file.disabled = false;
    }
  });
  if (game.phase === 'season_over') {
    return h('section', { class: 'order-box', 'aria-label': 'Season over' },
      h('span', { class: 'eyebrow' }, 'Season over · no more orders'));
  }
  return form;
}

function agentPanel(view) {
  const a = view.seat.agent;
  const name = a?.name ?? 'Your agent';
  const log = h('div', { class: 'chat', role: 'log', tabindex: 0, 'aria-label': `Conversation with ${name}` },
    ui.asked.length ? ui.asked.map((m) => h('div', { class: m.me ? 'me' : 'them' }, m.me ? m.text : reply(m.text)))
      : h('p', { class: 'muted small' }, `Ask ${name} anything about your position. Questions file nothing.`));
  const input = h('input', { id: 'wr-ask', type: 'text', maxlength: 1000, placeholder: `Ask ${name} anything` });
  const ask = h('button', { type: 'submit' }, 'Ask');
  const form = h('form', { class: 'ask', 'aria-label': `Ask ${name}` },
    h('label', { for: 'wr-ask', class: 'sr-only' }, `Ask ${name}`), input, ask);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    ui.asked.push({ me: true, text: q });
    input.value = '';
    ask.disabled = true;
    const thinking = h('div', { class: 'them muted' }, `${name} is thinking…`);
    clear(log).append(...ui.asked.map((m) => h('div', { class: m.me ? 'me' : 'them' }, m.me ? m.text : reply(m.text))), thinking);
    try {
      const r = await api.seat('ask', { question: q });
      ui.asked.push({ me: false, text: r.reply });
    } catch (ex) {
      ui.asked.push({ me: false, text: ex.message });
    }
    ask.disabled = false;
    clear(log).append(...ui.asked.map((m) => h('div', { class: m.me ? 'me' : 'them' }, m.me ? m.text : reply(m.text))));
    log.scrollTop = log.scrollHeight;
  });
  return h('section', { class: 'agent', 'aria-label': name },
    h('div', { class: 'agent-head' },
      h('div', { class: 'portrait', 'aria-hidden': 'true' }, name.slice(0, 1)),
      h('div', { class: 'stack' }, h('span', { class: 'agent-name' }, name),
        h('span', { class: 'muted small' }, `${a?.role ?? ''} · advice is free`))),
    log, form);
}

function chronicle(view) {
  return h('section', { class: 'chronicle', 'aria-label': 'The Chronicle', tabindex: 0 },
    h('div', { class: 'sec-head' }, h('h2', {}, 'The Chronicle'), visTag('public', { upper: true })),
    view.statements.length
      ? h('ol', {}, view.statements.map((s) => h('li', {},
        h('span', { class: 'muted small' }, `Tick ${s.tick}`), h('span', {}, s.text))))
      : h('p', { class: 'muted small' }, 'No public statements yet.'));
}

let rerender = () => {};

export function warRoom(ctx) {
  const { me, refresh } = ctx;
  const game = me.game;
  const view = game.view;
  const root = h('div', { class: 'screen war-screen' });
  rerender = () => root.replaceChildren(...build());

  function build() {
    const tabs = [['briefing', 'Briefing'], ['dossier', 'Dossier'], ['map', 'Map'], ['ledger', 'Ledger']];
    const tablist = h('div', { role: 'tablist', 'aria-label': 'Workspace', class: 'tabs' },
      tabs.map(([k, label]) => h('button', {
        type: 'button', role: 'tab', 'aria-selected': String(ui.tab === k), id: `tab-${k}`,
        onclick: () => { ui.tab = k; rerender(); },
      }, label)));
    const body = { briefing: () => briefingTab(view, game), dossier: () => dossierTab(view), map: () => notLive('Map'), ledger: () => notLive('Ledger') }[ui.tab]();
    const next = game.paused ? 'Paused' : untilText(game.next_tick_at);
    const extra = [
      h('span', { class: 'divider', 'aria-hidden': 'true' }),
      h('div', { class: 'stack' }, h('span', { class: 'title' }, view.seat.name), h('span', { class: 'eyebrow' }, view.seat.agent ? `${view.seat.agent.name}, ${view.seat.agent.role}` : '')),
      h('span', { class: 'divider', 'aria-hidden': 'true' }),
      h('div', { class: 'stack' }, h('span', { class: 'title' }, worldDate(game.date) || `Tick ${game.tick}`), h('span', { class: 'eyebrow' }, `Tick ${game.tick} · ${game.world_days_per_tick} days a turn`)),
      game.phase === 'play' ? h('div', { class: 'stack' }, h('span', { class: 'eyebrow' }, 'Next tick in'), h('span', { class: 'title num' }, next)) : h('div', { class: 'stack' }, h('span', { class: 'eyebrow' }, 'Ticks'), h('span', { class: 'title' }, 'Season over')),
      h('div', { class: 'stack' }, h('span', { class: 'eyebrow' }, 'Divergence'), h('span', { class: 'title num' }, String(game.divergence ?? 0))),
    ];
    return [
      header({ ...ctx, game, role: 'Campaign', extra }),
      contestBand(view.contests),
      h('div', { class: 'war' },
        position(view),
        h('main', { class: 'col-centre' }, tablist, h('div', { role: 'tabpanel', 'aria-labelledby': `tab-${ui.tab}`, class: 'tabpanel', tabindex: 0 }, body), orderBox(view, game, refresh)),
        h('aside', { class: 'col-right', 'aria-label': 'Agent and the Chronicle' }, agentPanel(view), chronicle(view))),
    ];
  }
  root.append(...build());
  return root;
}
