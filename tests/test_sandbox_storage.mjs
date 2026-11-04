// Offline regression: sandboxed iframe storage getters throw SecurityError.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../pages/management/app.js', import.meta.url), 'utf8');
function declaration(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1);
  const end = source.indexOf('\n}\n', start);
  assert.notEqual(end, -1);
  return source.slice(start, end + 3);
}

function fixture(window) {
  const defaults = {}, groups = {};
  const context = vm.createContext({window, $$: () => [],
    $: selector => selector === '#push-view-defaults' ? defaults : groups});
  const functions = vm.runInContext(
    ['readSession', 'writeSession', 'activatePushView'].map(declaration).join('\n') +
    '\n({readSession, writeSession, activatePushView})', context);
  return {...functions, defaults, groups};
}

test('sandbox storage getter cannot prevent default view or switching groups', () => {
  const window = {};
  Object.defineProperty(window, 'sessionStorage', {get() {throw new Error('SecurityError');}});
  const app = fixture(window);
  assert.equal(app.readSession('cs2.pushView'), null);
  assert.doesNotThrow(() => app.activatePushView('defaults'));
  assert.equal(app.defaults.hidden, false);
  assert.equal(app.groups.hidden, true);
  assert.doesNotThrow(() => app.activatePushView('groups'));
  assert.equal(app.groups.hidden, false);
  assert.equal(app.defaults.hidden, true);
});

test('blocked getItem and quota-limited setItem are optional', () => {
  const app = fixture({sessionStorage: {
    getItem() {throw new Error('Blocked');},
    setItem() {throw new Error('QuotaExceededError');},
  }});
  assert.equal(app.readSession('cs_panel_token'), null);
  assert.doesNotThrow(() => app.writeSession('cs_panel_token', 'offline-placeholder'));
  assert.doesNotThrow(() => app.activatePushView('groups'));
});

test('normal browser storage continues remembering view and token', () => {
  const values = new Map();
  const app = fixture({sessionStorage: {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }});
  app.activatePushView('groups');
  assert.equal(app.readSession('cs2.pushView'), 'groups');
  app.writeSession('cs_panel_token', 'offline-placeholder');
  assert.equal(app.readSession('cs_panel_token'), 'offline-placeholder');
});
