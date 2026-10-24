// A realism game choosing its people (Batch 5): each player picks someone from the
// cast, whose papers are drawn up on the spot if no one has yet. The host invites
// the others and begins.

import { api } from '../api.js';
import { h, reply } from '../dom.js';
import { castLine } from '../realism-rules.js';
import { header } from './frame.js';

async function run(out, button, label, action, body, after) {
  const was = button.textContent;
  button.disabled = true;
  button.textContent = label;
  out.replaceChildren();
  try {
    const r = await api.newGame(action, body);
    out.replaceChildren(reply(r.reply), r.code ? h('p', { class: 'code' }, h('strong', {}, r.code)) : null);
    if (!r.refused) await after?.(r);
  } catch (ex) {
    out.replaceChildren(h('p', { class: 'form-error' }, ex.message));
  } finally {
    button.disabled = false;
    button.textContent = was;
  }
}

function hostPanel(game, refresh) {
  const out = h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
  const invite = h('button', { type: 'button' }, 'Invite a player');
  invite.addEventListener('click', () => run(out, invite, 'Issuing…', 'invite', {}));
  const begin = h('button', { type: 'button', class: 'primary', disabled: game.waiting > 0 }, 'Begin the game');
  begin.addEventListener('click', () => run(out, begin, 'Beginning…', 'begin', {}, refresh));
  return h('aside', { class: 'aside-card', 'aria-label': 'Host' },
    h('span', { class: 'eyebrow' }, 'You are the host'),
    h('p', {}, castLine(game.waiting)),
    h('div', { class: 'row' }, invite, begin),
    h('p', { class: 'hint' }, 'Each code seats one player. Anyone in the cast no player takes is drawn up after the game begins and acts on their own.'),
    out);
}

export function realismCast(ctx) {
  const { me, refresh } = ctx;
  const game = me.game;
  const view = game.view;
  const mine = view.seat?.name;
  const out = h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
  const cards = view.roster.map((c) => {
    const play = h('button', { type: 'button', disabled: c.taken || Boolean(mine) },
      c.taken ? 'Taken' : `Play ${c.name}`);
    play.addEventListener('click', () => run(out, play, 'Drawing up their papers…', 'pick', { name: c.name }, refresh));
    return h('article', { class: `char${c.taken ? ' taken' : ''}` },
      h('div', { class: 'char-head' },
        h('span', { class: 'char-name' }, c.name),
        c.taken ? h('span', { class: 'tag' }, c.name === mine ? 'You' : 'Taken') : null),
      h('p', {}, c.pitch),
      h('p', { class: 'muted' }, h('strong', {}, 'Reach: '), c.reach),
      mine ? null : play);
  });
  return h('div', { class: 'screen' },
    header({ ...ctx, game, role: 'Choosing the cast' }),
    h('div', { class: 'sz sz-solo' },
      h('main', { class: 'sz-main' },
        h('div', { class: 'sz-inner' },
          h('div', { class: 'step-head' },
            h('h1', {}, mine ? `You are ${mine}` : 'Choose who you will play'),
            h('p', {}, mine
              ? 'Waiting for the host to begin. You can close this tab; your place is kept.'
              : 'Drawing up a person for the first time takes a minute.')),
          h('div', { class: 'split' },
            h('div', {}, out, h('div', { class: 'roster' }, cards)),
            game.host ? hostPanel(game, refresh) : null)))));
}
