import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../pages/management/presets.js', import.meta.url));
const { BUILTIN_PRESETS, applyPresetToGroup, presetRules } = await import(
  `data:text/javascript;base64,${source.toString('base64')}`);

test('built-in presets enable both Major notices and keep group blacklists', () => {
  const current = { blacklist_teams: ['bad team'], blacklist_leagues: ['blocked league'], push_enabled: false, followed_teams: [{id: 5, name: 'NAVI'}] };
  for (const preset of BUILTIN_PRESETS) {
    const result = presetRules(preset, current, { builtin: true });
    assert.equal(result.major_stage_notify_enabled, true);
    assert.equal(result.tournament_announce_enabled, true);
    assert.equal(result.tournament_announce_hours, 2);
    assert.equal(result.blacklist_teams[0], 'bad team');
    assert.equal(result.push_enabled, false);
  }
  assert.deepEqual(presetRules(BUILTIN_PRESETS[1], current, { builtin: true }).followed_teams, current.followed_teams);
});

test('custom presets replay their saved teams and blacklists exactly', () => {
  const preset = {id: 'my-preset', rules: {filter_mode: 'followed', followed_teams: [{id: 7, name: 'Spirit'}], blacklist_teams: ['saved'], push_enabled: true}};
  const result = presetRules(preset, {followed_teams: [{id: 5, name: 'NAVI'}], blacklist_teams: ['target'], push_enabled: false});
  assert.deepEqual(result.followed_teams, preset.rules.followed_teams);
  assert.deepEqual(result.blacklist_teams, ['saved']);
  assert.equal(result.push_enabled, true);
});

test('applying one group preset preserves every other group draft', () => {
  const groups = {old: {remind_minutes: 42}, target: {blacklist_teams: ['local']}};
  const preset = {id: 'builtin-big-event', rules: {filter_mode: 'tiers', min_tiers: ['s'], major_stage_notify_enabled: true}};
  const result = applyPresetToGroup(groups, 'target', preset, {blacklist_teams: ['local'], push_enabled: true}, {builtin: true});
  assert.deepEqual(result.old, groups.old);
  assert.deepEqual(result.target.blacklist_teams, ['local']);
  assert.deepEqual(groups.target, {blacklist_teams: ['local']});
});
