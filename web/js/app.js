// The shell: asks the server who is signed in and what their seat sees, then
// draws the one screen that fits. No framework, no build step.

import { api } from './api.js';
import { h } from './dom.js';
import { join, recover, recoveryCodes, signIn, signUp } from './views/auth.js';
import { newGame } from './views/new-game.js';
import { realismCast } from './views/realism-cast.js';
import { realismRoom } from './views/realism-room.js';
import { sessionZero } from './views/session-zero.js';
import { warRoom } from './views/war-room.js';

const root = document.getElementById('app');
let route = location.hash.slice(1) || 'signin';
let pendingCodes = null;

function go(next) {
  route = next;
  history.replaceState(null, '', `#${next}`);
  render();
}

/** Widths and positions come from data attributes: the CSP allows no inline style. */
function applyGeometry(el) {
  for (const node of el.querySelectorAll('[data-pct]')) {
    const pct = Math.max(0, Math.min(100, Number(node.dataset.pct) || 0));
    if (node.classList.contains('marker')) node.style.left = `${pct}%`;
    else node.style.width = `${pct}%`;
  }
}

function show(el) {
  root.replaceChildren(el);
  applyGeometry(el);
  const heading = el.querySelector('h1');
  if (heading && !el.contains(document.activeElement)) {
    heading.setAttribute('tabindex', '-1');
  }
}

async function signOut() {
  try {
    await api.signout();
  } finally {
    go('signin');
  }
}

function problem(message, retry) {
  return h('main', { class: 'auth' }, h('div', { class: 'auth-card', role: 'alert' },
    h('h1', {}, 'The game server is not answering'),
    h('p', {}, message),
    h('button', { type: 'button', class: 'primary', onclick: retry }, 'Try again')));
}

async function render() {
  if (pendingCodes) {
    const list = pendingCodes;
    show(recoveryCodes({ list, done: () => { pendingCodes = null; go('home'); } }));
    return;
  }
  let me;
  try {
    me = await api.me();
  } catch (ex) {
    show(problem(ex.message, render));
    return;
  }
  const codes = (list) => { pendingCodes = list; render(); };
  if (!me.account) {
    const views = { signup: () => signUp({ go, codes }), recover: () => recover({ go, codes }) };
    show((views[route] ?? (() => signIn({ go, done: () => go('home') })))());
    return;
  }
  if (me.error) {
    show(problem(me.error, render));
    return;
  }
  const refresh = async () => render();
  const ctx = { me, refresh, signOut, go };
  if (!me.game) {
    show(route === 'new' ? newGame(ctx) : join({ me, done: refresh, signOut, go }));
    return;
  }
  document.title = `${me.game.name} · Revisionist`;
  if (me.game.phase === 'session_zero') show(me.game.ruleset === 'realism' ? realismCast(ctx) : sessionZero(ctx));
  else show(me.game.ruleset === 'realism' ? realismRoom(ctx) : warRoom(ctx));
}

// Keep the clock and the briefing fresh without a reload: re-read every minute
// unless the player is typing.
setInterval(() => {
  const typing = document.activeElement?.matches?.('input, textarea');
  const dialogOpen = document.querySelector('dialog[open]');
  const working = document.querySelector('button[disabled].primary');
  if (!typing && !dialogOpen && !working && route !== 'signup' && !pendingCodes) render();
}, 60_000);

render();
