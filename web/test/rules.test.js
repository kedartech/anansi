// Unit tests for the rule guards. Run: node --test web/test/
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { BANDS, ORDER_VISIBILITIES, VISIBILITY, band, disposition, parseReply, untilText, visibility, worldDate } from '../js/rules.js';

const WEB = fileURLToPath(new URL('..', import.meta.url));

function sources(dir = join(WEB, 'js')) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? sources(path) : path.endsWith('.js') ? [path] : [];
  });
}

test('odds are exactly five bands, each a word and never a number', () => {
  assert.deepEqual(Object.keys(BANDS), ['impossible', 'long_shot', 'contested', 'likely', 'assured']);
  for (const b of Object.values(BANDS)) assert.doesNotMatch(b.word, /\d|%/);
  assert.throws(() => band('0.62'));
  assert.throws(() => band('62%'));
  assert.throws(() => band('__proto__'));
});

test('every visibility has an icon and a word', () => {
  assert.deepEqual(Object.keys(VISIBILITY), ['public', 'restricted', 'private', 'covert']);
  for (const v of Object.values(VISIBILITY)) {
    assert.ok(v.word.length > 0);
    assert.ok(v.icon.length > 0);
  }
  assert.throws(() => visibility('secret'));
  for (const v of ORDER_VISIBILITIES) assert.ok(VISIBILITY[v]);
});

test('replies parse to text tokens, never markup', () => {
  const blocks = parseReply('**H2** <img src=x onerror=alert(1)>\n- one\n- **two**');
  assert.equal(blocks[0].spans[0].text, 'H2');
  assert.equal(blocks[0].spans[0].strong, true);
  assert.equal(blocks[0].spans[1].text, ' <img src=x onerror=alert(1)>');
  assert.equal(blocks[1].kind, 'list');
  assert.equal(blocks[1].items.length, 2);
  // An unclosed ** stays literal rather than bolding the rest.
  assert.equal(parseReply('a ** b')[0].spans.every((s) => !s.strong), true);
});

test('no client code can parse a string as HTML', () => {
  const banned = /\.innerHTML|\.outerHTML|insertAdjacentHTML|document\.write|new Function|\beval\(|DOMParser|createContextualFragment/;
  for (const path of sources()) assert.doesNotMatch(readFileSync(path, 'utf8'), banned, path);
});

test('no client code reaches outside its own origin', () => {
  for (const path of [...sources(), join(WEB, 'index.html'), join(WEB, 'css', 'app.css'), join(WEB, 'css', 'fonts.css')]) {
    assert.doesNotMatch(readFileSync(path, 'utf8'), /https?:\/\/(?!www\.w3\.org\/2000\/svg)/, path);
  }
});

test('no client code tells a human seat from an AI one', () => {
  for (const path of sources()) {
    const text = readFileSync(path, 'utf8');
    assert.doesNotMatch(text, /\b(controller|is_human|isHuman|is_ai|\.human\b|\btruth\b)/, path);
  }
});

test('disposition words match the dossier', () => {
  assert.deepEqual([-4, -3, -2, -1, 0, 1, 2, 3, 5].map(disposition),
    ['Hostile', 'Hostile', 'Cold', 'Cold', 'Neutral', 'Warm', 'Warm', 'Close', 'Close']);
});

test('times and dates read as words', () => {
  const now = Date.parse('2026-10-22T12:00:00Z');
  assert.equal(untilText('2026-10-23T02:22:00Z', now), '14 h 22 m');
  assert.equal(untilText('2026-10-22T12:09:00Z', now), '9 m');
  assert.equal(untilText('2026-10-22T11:00:00Z', now), 'Due now');
  assert.equal(worldDate('1944-06-06'), '6 June 1944');
});
