// Session Zero, the player's steps (design L6-L10): character, goal, brief, agent,
// then waiting for the host. The host's steps (pitch, card, confirm, begin) stay on
// Discord and the CLI; a web account is never the host.

import { api } from '../api.js';
import { h, icon, prose, reply, visTag } from '../dom.js';
import { GOAL_KINDS } from '../rules.js';
import { header } from './frame.js';

const STEPS = [
  ['character', 'Character'],
  ['goal', 'Secret goal'],
  ['brief', 'Historical brief'],
  ['agent', 'Your agent'],
  ['waiting', 'Waiting to begin'],
];
const PRIVATE_STEPS = new Set(['character', 'goal', 'brief']);

/** The first step not yet done, from what the engine says is done. */
export function currentStep(view) {
  const done = view.session_zero;
  if (!view.seat || !done) return 'character';
  if (!done.goal) return 'goal';
  if (!done.brief) return 'brief';
  if (!done.agent) return 'agent';
  return 'waiting';
}

function busy(button, label) {
  const was = button.textContent;
  button.disabled = true;
  button.textContent = label;
  return () => { button.disabled = false; button.textContent = was; };
}

function outcome() {
  return h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
}

async function run(out, button, label, action, body, after) {
  const restore = busy(button, label);
  out.replaceChildren();
  try {
    const r = await api.seat(action, body);
    out.replaceChildren(reply(r.reply));
    if (!r.refused) await after?.(r);
  } catch (ex) {
    out.replaceChildren(h('p', { class: 'form-error' }, ex.message));
  } finally {
    restore();
  }
}

function character(view, refresh) {
  const out = outcome();
  const query = h('input', { id: 'sz-search', type: 'search', maxlength: 400, placeholder: "e.g. someone who runs the Atlantic Wall" });
  const searchBtn = h('button', { type: 'submit' }, 'Search');
  const found = h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
  const search = h('form', { class: 'search', 'aria-label': 'Search the roster' },
    h('label', { for: 'sz-search' }, 'Describe who you want to play'),
    h('div', { class: 'row' }, query, searchBtn), found);
  search.addEventListener('submit', (e) => {
    e.preventDefault();
    if (query.value.trim()) run(found, searchBtn, 'Searching…', 'search', { text: query.value });
  });

  const cards = view.roster.map((c) => {
    const claim = h('button', { type: 'button', disabled: c.taken }, c.taken ? 'Taken' : `Play ${c.name}`);
    claim.addEventListener('click', () => run(out, claim, 'Claiming…', 'claim', { name: c.name }, refresh));
    return h('article', { class: `char${c.taken ? ' taken' : ''}` },
      h('div', { class: 'char-head' },
        h('span', { class: 'char-name' }, c.name),
        c.composite ? h('span', { class: 'tag' }, 'Composite') : null,
        c.taken ? h('span', { class: 'tag' }, 'Taken') : null),
      h('p', {}, c.pitch),
      h('p', { class: 'muted' }, h('strong', {}, 'Reach: '), c.reach),
      c.sides.length ? h('p', { class: 'muted' }, c.sides.map((s) => `${s.contest}: ${s.side}`).join(' · ')) : null,
      claim);
  });

  const name = h('input', { id: 'sz-name', type: 'text', maxlength: 120, placeholder: 'Anyone from the record' });
  const pickBtn = h('button', { type: 'submit' }, 'Check and choose');
  const pickOut = outcome();
  const pick = h('form', { class: 'aside-card', 'aria-label': 'Name anyone' },
    h('label', { for: 'sz-name' }, 'Or name anyone'),
    name, pickBtn, pickOut,
    h('p', { class: 'hint' }, 'The engine checks the record: they must be able to act at the start date.'));
  pick.addEventListener('submit', (e) => {
    e.preventDefault();
    if (name.value.trim()) run(pickOut, pickBtn, 'Checking the record…', 'pick', { name: name.value }, refresh);
  });

  return [
    h('div', { class: 'step-head' },
      h('h1', {}, 'Choose your character'),
      h('p', {}, 'Anyone on the roster who is free, or anyone from the record who could act at the start.')),
    h('div', { class: 'split' },
      h('div', {}, search, out, h('div', { class: 'roster' }, cards)),
      h('aside', {}, pick)),
  ];
}

function goal(view, refresh) {
  const out = outcome();
  const offers = view.session_zero?.offers ?? {};
  const lists = [];
  const routes = [['history', 'As history saw it'], ['could_have', 'What you could have done'], ['own', 'Your own']];
  for (const [route, title] of routes) {
    const lines = offers[route];
    if (!lines?.length) continue;
    lists.push(h('fieldset', { class: 'offers' },
      h('legend', {}, title),
      lines.map((line) => {
        const m = /^([HCO]\d+)\. (.*)$/.exec(line);
        const label = m ? m[1] : line;
        const text = m ? m[2] : line;
        const confirm = h('button', { type: 'button' }, `Choose ${label}`);
        confirm.addEventListener('click', () => run(out, confirm, 'Fixing…', 'confirm', { label }, refresh));
        return h('div', { class: 'offer' }, h('span', { class: 'label' }, label), h('span', {}, text), confirm);
      })));
  }
  const ask = (route, label) => {
    const b = h('button', { type: 'button' }, label);
    b.addEventListener('click', () => run(out, b, 'Your agent is thinking…', 'goal', { route }, refresh));
    return b;
  };
  const own = h('textarea', { id: 'sz-own', rows: 2, maxlength: 280 });
  const ownBtn = h('button', { type: 'submit' }, 'Check my goal');
  const ownForm = h('form', { class: 'own', 'aria-label': 'Write your own goal' },
    h('label', { for: 'sz-own' }, 'Or write your own'), own, ownBtn);
  ownForm.addEventListener('submit', (e) => {
    e.preventDefault();
    if (own.value.trim()) run(out, ownBtn, 'Your agent is reading it…', 'goal', { route: 'own', text: own.value }, refresh);
  });
  return [
    h('div', { class: 'step-head' },
      h('h1', {}, 'A secret goal'),
      h('p', {}, 'Nobody sees it until the Reveal. Your agent offers goals; you fix one for the season.'),
      h('p', { class: 'muted' },
        h('strong', {}, GOAL_KINDS.lasting), ': once met, it stays met. ',
        h('strong', {}, GOAL_KINDS.undoable), ": must still hold at season's end.")),
    h('div', { class: 'row' }, ask('history', 'Goals from history'), ask('could_have', 'Goals history missed')),
    lists,
    ownForm,
    out,
  ];
}

function brief(view, refresh) {
  const out = outcome();
  const b = h('button', { type: 'button', class: 'primary' }, 'Write my historical brief');
  b.addEventListener('click', () => run(out, b, 'Writing your brief…', 'history', {}, refresh));
  return [
    h('div', { class: 'step-head' },
      h('h1', {}, `${view.seat.name}: historical brief`),
      h('p', {}, 'What the record says up to the start, the goal as history saw it, and one route you could take.')),
    h('div', { class: 'row' }, b), out,
  ];
}

function briefSheet(view) {
  if (!view.brief) return null;
  return h('article', { class: 'paper brief', 'aria-label': 'Historical brief' },
    h('div', { class: 'paper-head' },
      h('div', {}, h('span', { class: 'eyebrow' }, 'Historical brief'), h('h2', {}, view.seat.name)),
      visTag('private', { upper: true })),
    prose(view.brief, 'prose dispatch'));
}

function agent(view, refresh) {
  const out = outcome();
  const f = (id, label, max, area = false) => h('div', { class: 'field' },
    h('label', { for: id }, label),
    area ? h('textarea', { id, name: id, rows: 2, maxlength: max }) : h('input', { id, name: id, maxlength: max }));
  const save = h('button', { type: 'submit', class: 'primary' }, 'Save agent');
  const form = h('form', { 'aria-label': 'Agent design' },
    f('ag-name', 'Name', 60), f('ag-role', 'Role', 60), f('ag-voice', 'Voice', 200),
    f('ag-back', 'Backstory', 400, true), f('ag-rel', `Relationship to ${view.seat.name}`, 200, true),
    save);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const v = (k) => form.elements[k].value;
    run(out, save, 'Saving…', 'agent', {
      name: v('ag-name'), role: v('ag-role'), voice: v('ag-voice'),
      backstory: v('ag-back'), relationship: v('ag-rel'),
    }, refresh);
  });
  return [
    h('div', { class: 'step-head' },
      h('h1', {}, 'Design your agent'),
      h('p', {}, 'Your aide writes your briefings, drafts your orders and plays your seat when you are away. It locks when play begins.')),
    form, out,
  ];
}

function waiting(view) {
  const s = view.seat;
  return [
    h('div', { class: 'step-head' },
      h('h1', {}, 'Waiting for the host to begin'),
      h('p', {}, "You're ready. You can close this tab; your seat waits for you.")),
    h('section', { class: 'paper seat-card', 'aria-label': 'Your seat' },
      h('div', { class: 'paper-head' }, h('span', { class: 'eyebrow' }, 'Your seat'), visTag('private')),
      h('h2', {}, s.name),
      h('dl', {},
        h('dt', {}, 'Goal'), h('dd', {}, s.goal ? `${s.goal.text} · ${GOAL_KINDS[s.goal.kind]}` : 'Not set'),
        h('dt', {}, 'Brief'), h('dd', {}, view.brief ? 'Written' : 'Not yet'),
        h('dt', {}, 'Agent'), h('dd', {}, s.agent ? `${s.agent.name}, ${s.agent.role}` : 'Not yet'))),
  ];
}

export function sessionZero(ctx) {
  const { me, refresh } = ctx;
  const game = me.game;
  const view = game.view;
  const step = currentStep(view);
  const rail = h('nav', { class: 'rail', 'aria-label': 'Session Zero steps' },
    h('span', { class: 'eyebrow' }, 'Your steps'),
    STEPS.map(([key, label], i) => {
      const idx = STEPS.findIndex(([k]) => k === step);
      const mark = i < idx ? icon('check', 12) : String(i + 1);
      return h('div', { class: 'rail-step', 'aria-current': key === step ? 'step' : null },
        h('span', { class: 'rail-mark' }, mark), h('span', {}, label),
        PRIVATE_STEPS.has(key) ? h('span', { class: 'rail-private', title: 'Private' }, icon('private', 11)) : null);
    }),
    h('p', { class: 'hint' }, 'Private steps are seen by you alone. The host never sees your character pick, goal or brief.'));

  const body = { character, goal, brief, agent, waiting }[step](view, refresh);
  return h('div', { class: 'screen' },
    header({ ...ctx, game, role: 'Session Zero' }),
    h('div', { class: 'sz' },
      rail,
      h('main', { class: 'sz-main' },
        h('div', { class: 'sz-inner' },
          body,
          step === 'agent' || step === 'waiting' ? briefSheet(view) : null))));
}
