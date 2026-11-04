// Exercise the editor's draft operations without a browser, server or QQ account.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../pages/management/group-rules.js', import.meta.url));
const presetSource = await readFile(new URL('../pages/management/presets.js', import.meta.url));
globalThis.PRESETS_MODULE = await import(`data:text/javascript;base64,${presetSource.toString('base64')}`);
const testableSource = source.toString().replace(
  "import { BUILTIN_PRESETS, newPresetId, presetRules } from './presets.js';",
  'const { BUILTIN_PRESETS, newPresetId, presetRules } = globalThis.PRESETS_MODULE;',
);
const { createGroupRuleEditor, effectiveRules, RULE_FIELDS } = await import(
  `data:text/javascript;base64,${Buffer.from(testableSource).toString('base64')}`);

function fixture() {
  const config = {
    push_groups: ['100001', '100002'], remind_minutes: 23,
    followed_teams: [{ id: 1, name: 'NAVI' }], min_tiers: ['s', 'a'],
    group_rules: { '100002': { remind_minutes: 30, image_push_enabled: false } },
  };
  let draft = structuredClone(config);
  const events = {};
  let pending, copyFrom = '100002', checkedTiers = [];
  const host = {
    innerHTML: '', scrollIntoView() {},
    addEventListener(name, callback) { events[name] = callback; },
    querySelector(selector) {
      return selector === '#rule-copy-from' ? { value: copyFrom } : { focus() {} };
    },
    querySelectorAll(selector) {
      if (selector === '[data-rule-tier]:checked') return checkedTiers;
      if (selector === '[data-preset-team]:checked') return [{ value: '7' }];
      return [];
    },
  };
  globalThis.document = { querySelector: () => host, getElementById: () => null };
  const toasts = [];
  const editor = createGroupRuleEditor({
    getConfig: () => config, getDraft: () => draft, getDefaults: () => config,
    edit(callback) { callback(draft); editor.render(); },
    api(path) {
      if (path === '/api/teams/popular') return Promise.resolve({ teams: [{ id: 7, name: 'NAVI' }] });
      throw new Error('No remote operations allowed');
    },
    run(_button, operation) { pending = Promise.resolve(operation()); return pending; },
    dialog: async () => ({}), escape: value => String(value ?? ''), toast: message => toasts.push(message),
  });
  editor.select('100001');
  return {
    config, draft, editor, host, toasts,
    abandon() { draft = config; editor.render(); },
    async mode(value) {
      pending = undefined;
      events.change({ target: { id: 'rule-mode', value } });
      await pending;
    },
    inherit(key, checked) { events.change({ target: { dataset: { ruleInherit: key }, checked } }); },
    value(key, value, valid = true) {
      const element = { dataset: { ruleValue: key }, value, error: '', reported: false,
        checkValidity: () => valid, reportValidity() { this.reported = true; },
        setCustomValidity(message) { this.error = message; } };
      events.change({ target: element });
      return element;
    },
    tiers(values) {
      checkedTiers = values.map(tier => ({ dataset: { ruleTier: tier } }));
      events.change({ target: { dataset: { ruleTier: 's' } } });
    },
    async click(attribute, dataset = {}) {
      const button = { dataset, hasAttribute: name => name === attribute };
      pending = undefined;
      events.click({ target: { closest: () => button } });
      await pending;
    },
  };
}

test('false and empty independent values survive default changes without aliasing', () => {
  const f = fixture();
  f.draft.group_rules['100001'] = { image_push_enabled: false, min_tiers: [], followed_teams: [] };
  const rules = effectiveRules(f.draft, '100001');
  assert.equal(rules.image_push_enabled, false);
  assert.deepEqual(rules.min_tiers, []);
  assert.deepEqual(rules.followed_teams, []);
  rules.followed_teams.push({ id: 7, name: 'Local' });
  assert.deepEqual(f.draft.group_rules['100001'].followed_teams, []);
});

test('independent reminder and restored inheritance do not change another group', () => {
  const f = fixture();
  f.inherit('remind_minutes', true);
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 23);
  f.value('remind_minutes', '67');
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 67);
  assert.equal(f.draft.group_rules['100002'].remind_minutes, 30);
  f.inherit('remind_minutes', false);
  assert.equal(f.draft.group_rules['100001'], undefined);
  f.config.remind_minutes = 31;
  assert.equal(effectiveRules({ ...f.config, group_rules: f.draft.group_rules }, '100001').remind_minutes, 31);
});

test('an explicit disabled notification remains an override', () => {
  const f = fixture();
  f.inherit('finished_enabled', true);
  f.value('finished_enabled', 'false');
  assert.equal(f.draft.group_rules['100001'].finished_enabled, false);
  assert.equal(effectiveRules(f.draft, '100002').finished_enabled, true);
});

test('empty tiers and removing the last followed team remain explicit', async () => {
  const f = fixture();
  f.inherit('min_tiers', true);
  f.tiers([]);
  f.inherit('followed_teams', true);
  await f.click('data-rule-team-remove', { ruleTeamRemove: '0' });
  assert.deepEqual(f.draft.group_rules['100001'].min_tiers, []);
  assert.deepEqual(f.draft.group_rules['100001'].followed_teams, []);
  assert.deepEqual(f.config.followed_teams, [{ id: 1, name: 'NAVI' }]);
});

test('copy creates a full independent snapshot and reset affects only the selected group', async () => {
  const f = fixture();
  await f.click('data-rule-copy');
  assert.equal(Object.keys(f.draft.group_rules['100001']).length, RULE_FIELDS.length);
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 30);
  assert.equal(f.draft.group_rules['100001'].image_push_enabled, false);
  f.draft.group_rules['100002'].remind_minutes = 40;
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 30);
  await f.click('data-rule-reset');
  assert.equal(f.draft.group_rules['100001'], undefined);
  assert.equal(f.draft.group_rules['100002'].remind_minutes, 40);
});

test('switching groups preserves each draft without changing saved configuration', () => {
  const f = fixture();
  f.inherit('remind_minutes', true);
  f.value('remind_minutes', '17');
  f.editor.select('100002');
  f.value('remind_minutes', '29');
  f.editor.select('100001');
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 17);
  assert.equal(f.draft.group_rules['100002'].remind_minutes, 29);
  assert.equal(f.config.group_rules['100001'], undefined);
  assert.equal(f.config.group_rules['100002'].remind_minutes, 30);
});

test('invalid number or daily time never enters the draft', () => {
  const f = fixture();
  f.inherit('remind_minutes', true);
  const invalid = f.value('remind_minutes', '121', false);
  assert.equal(invalid.reported, true);
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 23);
  const time = f.value('daily_push_times', '25:00');
  assert.equal(time.reported, true);
  assert.match(time.error, /HH:MM/);
  assert.equal(f.draft.group_rules['100001'].daily_push_times, undefined);
});

test('editing a category keeps it active after the draft rerenders', async () => {
  const f = fixture();
  await f.click('', { ruleSection: 'notices' });
  f.inherit('remind_minutes', true);
  f.value('remind_minutes', '19');
  assert.match(f.host.innerHTML, /id="rule-tab-notices"[^>]*aria-selected="true"/);
  assert.match(f.host.innerHTML, /data-rule-value="remind_minutes"/);
  assert.doesNotMatch(f.host.innerHTML, /data-rule-value="daily_push_times"/);
});

test('applying a built-in preset through the editor applies the draft and reports status', async () => {
  const f = fixture();
  await f.click('data-preset-action', { presetAction: 'apply', presetId: 'builtin-big-event' });
  const applied = f.draft.group_rules['100001'];
  assert.equal(applied.filter_mode, 'tiers');
  assert.deepEqual(applied.min_tiers, ['s']);
  assert.equal(applied.major_stage_notify_enabled, true);
  assert.match(f.toasts[0], /已应用到群 100001/);
});

test('discarding a fan preset draft clears its pending team validation', async () => {
  const f = fixture();
  f.config.group_rules['100001'] = { filter_mode: 'followed', followed_teams: [] };
  f.config.followed_teams = [];
  await f.click('data-preset-action', { presetAction: 'apply', presetId: 'builtin-favorite-team' });
  await f.click('data-preset-action', { presetAction: 'apply-selected' });
  f.abandon();
  assert.doesNotThrow(() => f.editor.validate());
});

test('group independent mode snapshots defaults and can return to inheritance', async () => {
  const f = fixture();
  await f.mode('custom');
  assert.equal(Object.keys(f.draft.group_rules['100001']).length, RULE_FIELDS.length);
  f.config.remind_minutes = 45;
  assert.equal(effectiveRules({ ...f.config, group_rules: f.draft.group_rules }, '100001').remind_minutes, 23);
  assert.equal(f.draft.group_rules['100002'].remind_minutes, 30);
  f.value('remind_minutes', '18');
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 18);
  await f.mode('inherit');
  assert.equal(f.draft.group_rules['100001'], undefined);
  assert.equal(effectiveRules({ ...f.config, group_rules: f.draft.group_rules }, '100001').remind_minutes, 45);
});

test('existing partial rules keep inherited values editable and put overrides in advanced settings', () => {
  const f = fixture();
  f.editor.select('100002');
  assert.deepEqual(f.draft.group_rules['100002'], { remind_minutes: 30, image_push_enabled: false });
  const main = f.host.innerHTML.split('<details class="rule-inheritance"')[0];
  assert.doesNotMatch(main, /data-rule-inherit=/);
  assert.match(f.host.innerHTML, /高级设置：逐项继承/);
  assert.match(main, /data-rule-value="filter_mode"[^>]*><option/);
  assert.doesNotMatch(main, /data-rule-value="filter_mode"[^>]*disabled/);
  f.value('filter_mode', 'tiers');
  assert.equal(f.draft.group_rules['100002'].filter_mode, 'tiers');
  assert.equal(f.draft.group_rules['100002'].remind_minutes, 30);
});
