// Sign up, sign in, recovery codes and recovery (design B1-B4).

import { api } from '../api.js';
import { h, icon } from '../dom.js';

function field(id, label, attrs = {}) {
  return h('div', { class: 'field' },
    h('label', { for: id }, label),
    h('input', { id, name: id, ...attrs }));
}

function frame(title, lede, form, foot) {
  return h('main', { class: 'auth' },
    h('div', { class: 'auth-card' },
      h('div', { class: 'brand' }, icon('mark', 28), h('span', {}, 'Revisionist')),
      h('h1', {}, title),
      lede ? h('p', { class: 'lede' }, lede) : null,
      form,
      foot ? h('p', { class: 'auth-foot' }, foot) : null));
}

function errorLine() {
  return h('p', { class: 'form-error', role: 'alert', hidden: true });
}

function showError(el, message) {
  el.textContent = message;
  el.hidden = !message;
}

function link(text, route, go) {
  return h('a', { href: `#${route}`, onclick: (e) => { e.preventDefault(); go(route); } }, text);
}

async function submitting(form, work) {
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  try {
    await work();
  } finally {
    button.disabled = false;
  }
}

export function signIn({ go, done }) {
  const err = errorLine();
  const form = h('form', { 'aria-label': 'Sign in', novalidate: true },
    field('handle', 'Handle', { autocomplete: 'username', required: true, maxlength: 32 }),
    field('password', 'Password', { type: 'password', autocomplete: 'current-password', required: true }),
    err,
    h('button', { type: 'submit', class: 'primary' }, 'Sign in'));
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    submitting(form, async () => {
      try {
        await api.signin(form.handle.value, form.password.value);
        done();
      } catch (ex) {
        showError(err, ex.message);
      }
    });
  });
  return frame('Sign in', null, form,
    [link('Create an account', 'signup', go), ' · ', link('Lost your password?', 'recover', go)]);
}

export function signUp({ go, codes }) {
  const err = errorLine();
  const form = h('form', { 'aria-label': 'Create an account', novalidate: true },
    field('handle', 'Handle', { autocomplete: 'username', required: true, maxlength: 32, minlength: 3 }),
    h('p', { class: 'hint' }, 'Letters, digits, dots, dashes or underscores. Others at the table see it only in Session Zero.'),
    field('password', 'Password', { type: 'password', autocomplete: 'new-password', required: true, minlength: 12 }),
    h('p', { class: 'hint' }, 'At least 12 characters. A short sentence works well.'),
    err,
    h('button', { type: 'submit', class: 'primary' }, 'Create account'));
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    submitting(form, async () => {
      try {
        const out = await api.signup(form.handle.value, form.password.value);
        codes(out.codes);
      } catch (ex) {
        showError(err, ex.message);
      }
    });
  });
  return frame('Create an account', 'A handle and a password. No email, no real name.', form,
    ['Have one already? ', link('Sign in', 'signin', go)]);
}

export function recoveryCodes({ list, done }) {
  const text = list.join('\n');
  const copied = h('span', { class: 'hint', role: 'status' });
  const kept = h('input', { type: 'checkbox', id: 'kept' });
  const next = h('button', { type: 'button', class: 'primary', disabled: true, onclick: done }, 'Continue');
  kept.addEventListener('change', () => { next.disabled = !kept.checked; });
  const body = h('div', { class: 'codes-block' },
    h('ol', { class: 'codes' }, list.map((c) => h('li', {}, h('code', {}, c)))),
    h('div', { class: 'row' },
      h('button', {
        type: 'button',
        onclick: async () => {
          try {
            await navigator.clipboard.writeText(text);
            copied.textContent = 'Copied.';
          } catch {
            copied.textContent = 'Copy them by hand; the clipboard is blocked here.';
          }
        },
      }, 'Copy all'),
      copied),
    h('label', { class: 'check' }, kept, "I've kept these somewhere safe"),
    next);
  return frame('Your recovery codes',
    'Without email, these are the only way back in if you lose your password. Each works once. They are shown only now.',
    body, null);
}

export function recover({ go, codes }) {
  const err = errorLine();
  const form = h('form', { 'aria-label': 'Recover your account', novalidate: true },
    field('handle', 'Handle', { autocomplete: 'username', required: true }),
    field('code', 'Recovery code', { autocomplete: 'one-time-code', required: true, placeholder: 'XXXXX-XXXXX' }),
    field('password', 'New password', { type: 'password', autocomplete: 'new-password', required: true, minlength: 12 }),
    err,
    h('button', { type: 'submit', class: 'primary' }, 'Set new password'));
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    submitting(form, async () => {
      try {
        const out = await api.recover(form.handle.value, form.code.value, form.password.value);
        codes(out.codes);
      } catch (ex) {
        showError(err, ex.message);
      }
    });
  });
  return frame('Recover your account',
    'Spend one recovery code to set a new password. Every other browser is signed out, and you get ten fresh codes.',
    form, [link('Back to sign in', 'signin', go)]);
}

export function join({ me, done, signOut, go }) {
  const err = errorLine();
  const result = h('div', { role: 'status' });
  const form = h('form', { 'aria-label': 'Join a game', novalidate: true },
    field('code', 'Invite code', { required: true, autocomplete: 'off', placeholder: 'From the host' }),
    err,
    h('button', { type: 'submit', class: 'primary' }, 'Join'));
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    submitting(form, async () => {
      showError(err, '');
      try {
        const out = await api.seat('join', { code: form.code.value });
        if (out.refused) showError(err, out.reply);
        else done();
      } catch (ex) {
        showError(err, ex.message);
      }
    });
  });
  const live = me.live ?? [];
  const lede = live.length
    ? `${live.map((g) => g.name).join(', ')} is open. Ask the host for an invite code: /invite on Discord, or revisionist discord invite.`
    : 'No game is open yet. When the host starts one, they can send you an invite code.';
  return frame('Join a game', lede, h('div', {}, form, result),
    ['Signed in as ', h('strong', {}, me.account.handle), ' · ', link('Start a new game', 'new', go), ' · ',
      h('a', { href: '#', onclick: (e) => { e.preventDefault(); signOut(); } }, 'Sign out')]);
}
