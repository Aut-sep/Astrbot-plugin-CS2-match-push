// Exercise the editor's draft operations without a browser, server or QQ account.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../pages/management/group-rules.js', import.meta.url));
const { createGroupRuleEditor, effectiveRules, RULE_FIELDS } = await import(
  `data:text/javascript;base64,${source.toString('base64')}`);

function fixture() {
  const config = {
    push_groups: ['100001', '100002'], remind_minutes: 23,
    followed_teams: [{ id: 1, name: 'NAVI' }], min_tiers: ['s', 'a'],
    group_rules: { '100002': { remind_minutes: 30, image_push_enabled: false } },
  };
  const draft = structuredClone(config);
  const events = {};
  let pending, copyFrom = '100002', checkedTiers = [];
  const host = {
    innerHTML: '', scrollIntoView() {},
    addEventListener(name, callback) { events[name] = callback; },
    querySelector(selector) {
      return selector === '#rule-copy-from' ? { value: copyFrom } : { focus() {} };
    },
    querySelectorAll(selector) {
      return selector === '[data-rule-tier]:checked' ? checkedTiers : [];
    },
  };
  globalThis.document = { querySelector: () => host, getElementById: () => null };
  const editor = createGroupRuleEditor({
    getConfig: () => config, getDraft: () => draft, getDefaults: () => config,
    edit(callback) { callback(draft); editor.render(); },
    api() { throw new Error('No remote operations allowed'); },
    run(_button, operation) { pending = Promise.resolve(operation()); return pending; },
    dialog: async () => ({}), escape: value => String(value ?? ''),
  });
  editor.select('100001');
  return {
    config, draft, editor, host,
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
