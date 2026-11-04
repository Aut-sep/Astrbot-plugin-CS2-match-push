// Offline checks for the unified configuration editor; no browser or remote service.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
const presetSource = await readFile(new URL('../pages/management/presets.js', import.meta.url));
globalThis.PRESETS_MODULE = await import(`data:text/javascript;base64,${presetSource.toString('base64')}`);
const source = (await readFile(new URL('../pages/management/group-rules.js', import.meta.url))).toString().replace(
  "import { BUILTIN_PRESETS, getDefaultPreset, newPresetId, presetRules } from './presets.js';",
  'const { BUILTIN_PRESETS, getDefaultPreset, newPresetId, presetRules } = globalThis.PRESETS_MODULE;');
const { createGroupRuleEditor, effectiveRules, RULE_FIELDS } = await import(`data:text/javascript;base64,${Buffer.from(source + '\n//# sourceURL=group-editor-test.js').toString('base64')}`);

function fixture(clean = false) {
  const config = { push_groups: ['100001', '100002'], remind_minutes: 23,
    followed_teams: [{id: 1, name: 'NAVI'}], min_tiers: ['s','a'], rule_presets: [],
    group_rules: {'100002': {remind_minutes: 30, image_push_enabled: false}} };
  let draft = clean ? config : structuredClone(config), defaults = null, pending, dialogName = '我的配置';
  const events = {}, toasts = [];
  const host = {innerHTML: '', scrollIntoView() {},
    addEventListener(name, fn) {events[name] = fn;},
    querySelector() {return {focus() {}};},
    querySelectorAll(selector) {return selector === '[data-preset-team]:checked' ? [{value: '7'}] : [];}};
  globalThis.document = {querySelector: () => host, getElementById: () => null};
  const getDefaults = () => ({...config, ...defaults});
  function makeDraft() {if (draft === config) draft = structuredClone(config);}
  const editor = createGroupRuleEditor({getConfig: () => config, getDraft: () => draft, getDefaults,
    edit(fn) {makeDraft(); fn(draft); editor.render();},
    editDefaults(values) {
      makeDraft();
      if (!defaults) for (const gid of draft.push_groups) draft.group_rules[gid] = effectiveRules({...getDefaults(), group_rules: draft.group_rules}, gid);
      defaults = structuredClone(values); editor.render();
    },
    api(path) {assert.equal(path, '/api/teams/popular'); return Promise.resolve({teams: [{id: 7, name: 'Spirit'}]});},
    run(_button, fn) {pending = Promise.resolve(fn()); return pending;},
    dialog: async () => ({name: dialogName}), escape: value => String(value ?? ''), toast: text => toasts.push(text)});
  editor.render();
  return {config, editor, host, toasts, get draft() {return draft;}, get defaults() {return defaults;},
    name(value) {dialogName = value;},
    abandon() {draft = config; defaults = null; editor.render();},
    value(key, value, valid = true) {
      const element = {dataset: {ruleValue: key}, value, error: '', checkValidity: () => valid,
        reportValidity() {this.reported = true;}, setCustomValidity(message) {this.error = message;}};
      events.change({target: element}); return element;
    },
    toggle(checked) {events.change({target: {type: 'checkbox', checked, dataset: {ruleValue: 'push_enabled'}}});},
    async click(action, id) {
      const dataset = action === 'toggle' ? {ruleToggle: 'push_enabled'} : {presetAction: action, presetId: id};
      const button = {dataset, hasAttribute: () => false}; pending = undefined;
      events.click({target: {closest: () => button}}); await pending;
    }};
}

test('default configuration shares the library and editor without extra modes or inheritance controls', () => {
  const f = fixture();
  assert.match(f.host.innerHTML, /data-preset-id="builtin-default"/);
  assert.doesNotMatch(f.host.innerHTML, /rule-mode|data-rule-inherit|data-rule-copy|data-rule-reset|role="tablist"/);
  for (const title of ['赛事范围','比赛通知','赛程日报','消息样式']) assert.ok(f.host.innerHTML.includes(title));
});

test('applying the default configuration creates an independent group snapshot', async () => {
  const f = fixture(); f.editor.select('100001');
  await f.click('apply', 'builtin-default');
  assert.equal(Object.keys(f.draft.group_rules['100001']).length, RULE_FIELDS.length);
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 23);
  await f.click('edit-default'); f.value('remind_minutes', '45');
  assert.equal(f.defaults.remind_minutes, 45);
  assert.equal(f.draft.group_rules['100001'].remind_minutes, 23);
  assert.equal(f.draft.group_rules['100002'].remind_minutes, 30);
  assert.equal(f.config.remind_minutes, 23);
});

test('editing an existing partial group preserves its effective values as a complete configuration', () => {
  const f = fixture(); f.editor.select('100002'); f.value('remind_minutes', '18');
  assert.equal(Object.keys(f.draft.group_rules['100002']).length, RULE_FIELDS.length);
  assert.equal(f.draft.group_rules['100002'].image_push_enabled, false);
  f.config.min_tiers = ['c'];
  assert.deepEqual(f.draft.group_rules['100002'].min_tiers, ['s','a']);
  assert.equal(f.config.group_rules['100002'].remind_minutes, 30);
});

test('default edits can be saved as a named configuration, renamed and removed independently', async () => {
  const f = fixture(); f.value('remind_minutes', '19');
  await f.click('save-current');
  const saved = f.draft.rule_presets[0];
  assert.equal(saved.rules.remind_minutes, 19);
  assert.deepEqual(Object.keys(saved).sort(), ['id','name','rules']);
  f.name('周末观赛'); await f.click('rename', saved.id);
  assert.equal(f.draft.rule_presets[0].name, '周末观赛');
  await f.click('delete', saved.id);
  assert.deepEqual(f.draft.rule_presets, []);
  assert.equal(f.defaults.remind_minutes, 19);
});

test('copying a saved fan configuration replays its saved team and does not alias another group', async () => {
  const f = fixture();
  f.config.followed_teams = [{id: 7, name: 'Spirit'}];
  f.value('filter_mode', 'followed'); await f.click('save-current');
  const saved = f.draft.rule_presets[0];
  f.editor.select('100002'); await f.click('apply', saved.id);
  assert.deepEqual(f.draft.group_rules['100002'].followed_teams, [{id: 7, name: 'Spirit'}]);
  f.draft.group_rules['100002'].followed_teams.length = 0;
  assert.equal(saved.rules.followed_teams.length, 1);
});

test('fan selection stays visible before any draft exists and discard clears transient validation', async () => {
  const f = fixture(true); f.config.followed_teams = [];
  f.editor.select('100001'); await f.click('apply', 'builtin-favorite-team');
  assert.match(f.host.innerHTML, /data-preset-team/);
  await f.click('apply-selected');
  assert.deepEqual(f.draft.group_rules['100001'].followed_teams, [{id: 7, name: 'Spirit', slug: ''}]);
  f.abandon(); assert.doesNotThrow(() => f.editor.validate());
});

test('the master switch is directly usable and changes only the selected group', async () => {
  const f = fixture(); f.editor.select('100001'); f.toggle(false);
  assert.equal(f.draft.group_rules['100001'].push_enabled, false);
  assert.equal(f.draft.group_rules['100002'].push_enabled, undefined);
  assert.equal(f.config.group_rules['100001'], undefined);
});

test('invalid reminder and daily times are rejected before entering the configuration', () => {
  const f = fixture(); f.editor.select('100001');
  assert.equal(f.value('remind_minutes', '121', false).reported, true);
  assert.equal(f.draft.group_rules['100001'], undefined);
  const invalid = f.value('daily_push_times', '25:00');
  assert.equal(invalid.reported, true); assert.match(invalid.error, /HH:MM/);
  assert.equal(f.draft.group_rules['100001'], undefined);
});
