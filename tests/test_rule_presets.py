"""Offline persistence and isolation checks for reusable notification presets."""
from copy import deepcopy
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock

from test_group_rules import main, Rules, config, web, A, B


class RulePresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = main.DataStore(str(Path(self.temp.name) / 'state.json'))
        self.store.import_config({'push_groups': [A, B]})

    def preset(self):
        return {'id': 'my-matches', 'name': '我的观赛配置',
                'rules': {**Rules(self.store).values, 'filter_mode': 'tiers',
                          'major_stage_notify_enabled': True}}

    def test_save_reload_rename_and_delete_leave_applied_group_independent(self):
        preset = self.preset()
        self.store.import_config({'rule_presets': [preset], 'group_rules': {A: preset['rules']}})
        since = deepcopy(self.store.get('group_rule_since'))
        preset['name'] = '周末观赛'
        preset['rules']['remind_minutes'] = 25
        self.store.import_config({'rule_presets': [preset]})
        saved = main.DataStore(self.store.path)
        self.assertEqual(saved.export_all()['rule_presets'][0]['name'], '周末观赛')
        self.assertEqual(Rules(saved, A).get('remind_minutes'), 10)
        self.assertEqual(Rules(saved, B).get('remind_minutes'), 10)
        self.assertEqual(saved.get('group_rule_since'), since)
        saved.import_config({'rule_presets': []})
        self.assertTrue(saved.get('group_rules')[A]['major_stage_notify_enabled'])
        self.assertEqual(main.DataStore(saved.path).get('rule_presets'), [])

    def test_preset_rejects_credentials_and_nested_configuration_atomically(self):
        for key, value in [('pandascore_token', 'secret'), ('push_groups', [B]),
                           ('rule_presets', []), ('group_rules', {})]:
            with self.subTest(key=key):
                preset = self.preset()
                preset['rules'][key] = value
                with self.assertRaises(config.ConfigError):
                    self.store.import_config({'rule_presets': [preset]})
                self.assertEqual(main.DataStore(self.store.path).get('rule_presets'), [])

    def test_duplicate_name_invalid_rules_and_empty_fan_selection_rejected(self):
        first = self.preset()
        second = deepcopy(first)
        second['id'] = 'second'
        with self.assertRaises(config.ConfigError):
            self.store.import_config({'rule_presets': [first, second]})
        reserved = {**first, 'id': 'builtin-big-event'}
        with self.assertRaises(config.ConfigError):
            self.store.import_config({'rule_presets': [reserved]})
        for patch in [{'remind_minutes': 0}, {'major_stage_notify_enabled': 'true'},
                      {'filter_mode': 'followed', 'followed_teams': []}]:
            candidate = self.preset()
            candidate['rules'].update(patch)
            with self.assertRaises(config.ConfigError):
                self.store.import_config({'rule_presets': [candidate]})

    def test_import_export_preserves_presets_without_exposing_credentials(self):
        self.store.import_config({'pandascore_token': 'private-token',
                                  'rule_presets': [self.preset()]})
        exported = self.store.export_all()
        self.assertNotIn('pandascore_token', exported)
        other = main.DataStore(str(Path(self.temp.name) / 'other.json'))
        other.import_config(exported)
        self.assertEqual(other.get('rule_presets'), self.store.get('rule_presets'))
        self.assertTrue(Rules(other, A).enabled('major_stage'))


class RulePresetAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_import_migrates_legacy_windows_at_every_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            store = main.DataStore(str(Path(temp) / 'state.json'))
            plugin = types.SimpleNamespace(store=store, reload_runtime_config=AsyncMock(),
                rebuild_all_match_tasks=Mock(), rebuild_tournament_tasks=Mock())
            preset = {'id': 'custom', 'name': '观赛', 'rules': {**Rules(store).values, 'daily_push_days': 14}}
            backup = {'fetch_ahead_hours': 336, 'daily_push_days': 14, 'push_groups': [A],
                      'group_rules': {A: {'daily_push_days': 14}}, 'rule_presets': [preset]}
            req = types.SimpleNamespace(can_read_body=True, json=AsyncMock(return_value=backup))
            panel = web.WebPanel(plugin)
            with self.assertRaises(config.ConfigError):
                await panel.patch_config(req)
            self.assertEqual((await panel.import_config(req)).status, 200)
            self.assertEqual(store.get('fetch_ahead_days'), 7)
            self.assertEqual(store.get('daily_push_days'), 7)
            self.assertEqual(store.get('group_rules')[A]['daily_push_days'], 7)
            self.assertEqual(store.get('rule_presets')[0]['rules']['daily_push_days'], 7)

    async def test_saving_only_templates_does_not_refresh_or_rebuild_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            store = main.DataStore(str(Path(temp) / 'state.json'))
            plugin = types.SimpleNamespace(store=store, reload_runtime_config=AsyncMock(),
                rebuild_all_match_tasks=Mock(), rebuild_tournament_tasks=Mock(), schedule_refresh=Mock())
            preset = {'id': 'custom', 'name': '观赛', 'rules': Rules(store).values}
            req = types.SimpleNamespace(can_read_body=True,
                json=AsyncMock(return_value={'rule_presets': [preset]}))
            response = await web.WebPanel(plugin).patch_config(req)
            self.assertEqual(response.status, 200)
            plugin.reload_runtime_config.assert_not_awaited()
            plugin.rebuild_all_match_tasks.assert_not_called()
            plugin.rebuild_tournament_tasks.assert_not_called()
            plugin.schedule_refresh.assert_not_called()
            self.assertEqual(main.DataStore(store.path).get('rule_presets'), [preset])


if __name__ == '__main__':
    unittest.main()
