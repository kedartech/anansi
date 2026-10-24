// The black frame header shared by every signed-in screen (design: frame layer).

import { api } from '../api.js';
import { h, icon, reply } from '../dom.js';

/** `identity` is { name, office } of the person the player plays, shown under the game's name. */
export function header({ me, game, role, extra = [], refresh, signOut, identity = null }) {
  const bugDialog = bugReport(refresh);
  return h('header', { class: 'frame-head' },
    h('span', { class: 'brand-mark', 'aria-hidden': 'true' }, icon('mark', 22)),
    h('div', { class: 'stack' },
      h('span', { class: 'eyebrow' }, role),
      h('span', { class: 'title' }, game?.name ?? 'Revisionist')),
    identity?.name
      ? h('div', { class: 'stack identity' },
        h('span', { class: 'eyebrow' }, 'Playing as'),
        h('span', { class: 'title' }, identity.name),
        identity.office ? h('span', { class: 'muted office' }, identity.office) : null)
      : null,
    ...extra,
    h('div', { class: 'head-right' },
      h('span', { class: 'muted' }, 'Signed in as ', me.account.handle),
      game ? h('button', {
        type: 'button', class: 'icon-btn', 'aria-label': 'Report a bug', title: 'Report a bug',
        onclick: () => bugDialog.showModal(),
      }, icon('bug', 16)) : null,
      h('button', { type: 'button', class: 'ghost', onclick: signOut }, icon('out', 14), 'Sign out')),
    bugDialog);
}

function bugReport() {
  const out = h('div', { role: 'status' });
  const notes = h('textarea', { id: 'bug-notes', rows: 5, maxlength: 4000, required: true });
  const dialog = h('dialog', { class: 'sheet', 'aria-labelledby': 'bug-title' });
  const form = h('form', { method: 'dialog' },
    h('h2', { id: 'bug-title' }, 'Report a bug'),
    h('p', { class: 'hint' }, 'Pinned to this exact moment in the game. Only the host reads it.'),
    h('label', { for: 'bug-notes' }, 'What went wrong'),
    notes,
    out,
    h('div', { class: 'row end' },
      h('button', { type: 'button', class: 'ghost', onclick: () => dialog.close() }, 'Close'),
      h('button', { type: 'submit', class: 'primary' }, 'File it')));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!notes.value.trim()) return;
    out.replaceChildren('Filing…');
    try {
      const r = await api.seat('bug', { notes: notes.value });
      out.replaceChildren(reply(r.reply));
      if (!r.refused) notes.value = '';
    } catch (ex) {
      out.replaceChildren(ex.message);
    }
  });
  dialog.append(form);
  return dialog;
}
