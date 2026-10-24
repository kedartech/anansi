// A new realism game (Batch 5): a ready-made world, or a pitch read into start
// points and a generated cast. Whoever starts the game is its host.

import { api } from '../api.js';
import { h, reply } from '../dom.js';
import { header } from './frame.js';

function busy(button, label) {
  const was = button.textContent;
  button.disabled = true;
  button.textContent = label;
  return () => { button.disabled = false; button.textContent = was; };
}

async function run(out, button, label, action, body, after) {
  const restore = busy(button, label);
  out.replaceChildren();
  try {
    const r = await api.newGame(action, body);
    out.replaceChildren(reply(r.reply));
    if (!r.refused) await after?.(r);
  } catch (ex) {
    out.replaceChildren(h('p', { class: 'form-error' }, ex.message));
  } finally {
    restore();
  }
}

function readyMade(done) {
  const list = h('div', { class: 'roster' }, h('p', { class: 'muted' }, 'Loading…'));
  const out = h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
  api.newGame('worlds').then(({ worlds }) => {
    list.replaceChildren(...worlds.map((w) => {
      const start = h('button', { type: 'button' }, `Start ${w.name}`);
      start.addEventListener('click', () => run(out, start, 'Starting…', 'ready', { world: w.key }, done));
      return h('article', { class: 'char' },
        h('div', { class: 'char-head' }, h('span', { class: 'char-name' }, w.name)),
        h('p', {}, w.start), start);
    }));
  }).catch((ex) => list.replaceChildren(h('p', { class: 'form-error' }, ex.message)));
  return h('section', { 'aria-label': 'A ready-made game' },
    h('h2', {}, 'A ready-made game'),
    h('p', { class: 'muted' }, 'Every person in it is already drawn up.'),
    list, out);
}

function ownGame(done) {
  const out = h('div', { class: 'outcome', role: 'status', 'aria-live': 'polite' });
  const points = h('div', {});
  const pitch = h('textarea', { id: 'ng-pitch', rows: 2, maxlength: 200, placeholder: 'e.g. The Cuban missile crisis, from the first photographs' });
  const read = h('button', { type: 'submit' }, 'Read my pitch');
  const form = h('form', { 'aria-label': 'Your own game' },
    h('label', { for: 'ng-pitch' }, 'A war, a year, a person, a place'), pitch, read);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!pitch.value.trim()) return;
    points.replaceChildren();
    run(out, read, 'Reading…', 'pitch', { text: pitch.value }, (r) => {
      points.replaceChildren(h('fieldset', { class: 'offers' },
        h('legend', {}, 'Where to begin'),
        r.card.start_points.map((p, i) => {
          const take = h('button', { type: 'button' }, 'Begin here');
          take.addEventListener('click', () => run(out, take, 'Assembling the cast…', 'cast', { choice: i + 1 }, done));
          return h('div', { class: 'offer' },
            h('span', { class: 'label' }, String(i + 1)),
            h('span', {}, h('strong', {}, p.label), ' ', p.focus),
            take);
        })));
    });
  });
  return h('section', { 'aria-label': 'Your own game' },
    h('h2', {}, 'Your own'),
    h('p', { class: 'muted' }, 'Name a moment in history. You will choose where to begin, then who to play.'),
    form, out, points);
}

export function newGame(ctx) {
  const { refresh, go } = ctx;
  return h('div', { class: 'screen' },
    header({ ...ctx, game: null, role: 'New game' }),
    h('div', { class: 'sz sz-solo' },
      h('main', { class: 'sz-main' },
        h('div', { class: 'sz-inner' },
          h('div', { class: 'step-head' },
            h('h1', {}, 'Start a game'),
            h('p', {}, 'You will be its host: you invite the other players and say when it begins.'),
            h('p', {}, h('a', { href: '#home', onclick: (e) => { e.preventDefault(); go('home'); } }, 'Join a game with a code instead'))),
          readyMade(refresh),
          ownGame(refresh)))));
}
