"""Offline checks for host/panel edits and the three-day schedule limit."""
import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock

from test_start_notifications import main

sync = importlib.import_module('cs_start_checked.config_sync').sync_host_config
config = importlib.import_module('cs_start_checked.config')
ai = importlib.import_module('cs_start_checked.ai_queries')
previews = importlib.import_module('cs_start_checked.panel_previews')


class ConfigSyncTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / 'state.json')
        self.store = main.DataStore(self.path)
        self.host = {'pandascore_token': 'old-placeholder', 'fetch_ahead_days': 3,
                     'web_panel_port': 8765, 'web_panel_enabled': True,
                     'image_push_enabled': True, 'push_platform_id': ''}
        sync(self.store, self.host, first_run=True)

    def test_host_edits_apply_after_reload_including_false_and_empty(self):
        self.host.update(pandascore_token='', web_panel_port=9999,
                         web_panel_enabled=False, image_push_enabled=False,
                         push_platform_id='second-bot')
        loaded = main.DataStore(self.path)
        sync(loaded, self.host)
        for key, value in self.host.items():
            self.assertEqual(loaded.get(key), value)
        plugin = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        plugin._load_config(self.host)
        plugin.store = loaded
        plugin._sync_runtime_config_from_store()
        self.assertEqual(plugin._token, '')
        self.assertEqual(plugin._web_port, 9999)

    def test_unchanged_host_does_not_replay_over_panel_edits(self):
        self.store.import_config({'pandascore_token': 'panel-placeholder',
                                  'web_panel_port': 8888, 'image_push_enabled': False})
        loaded = main.DataStore(self.path)
        sync(loaded, self.host)
        self.assertEqual(loaded.get('pandascore_token'), 'panel-placeholder')
        self.assertEqual(loaded.get('web_panel_port'), 8888)
        self.assertFalse(loaded.get('image_push_enabled'))
        self.host['web_panel_port'] = 9999
        sync(loaded, self.host)
        self.assertEqual(loaded.get('web_panel_port'), 9999)
        self.assertEqual(loaded.get('pandascore_token'), 'panel-placeholder')

    def test_export_excludes_host_fingerprints_and_credentials(self):
        exported = self.store.export_all()
        self.assertNotIn('_host_config_fingerprints', exported)
        self.assertNotIn('pandascore_token', exported)

    def test_first_upgrade_preserves_panel_false_with_default_host_values(self):
        self.store.set('_host_config_fingerprints', {})
        self.store.import_config({'web_panel_enabled': False, 'image_push_enabled': False})
        sync(self.store, self.host)
        self.assertFalse(self.store.get('web_panel_enabled'))
        self.assertFalse(self.store.get('image_push_enabled'))

    def test_legacy_windows_are_capped_including_groups_and_presets(self):
        self.store.set('fetch_ahead_days', 30)
        self.store.set('daily_push_days', 7)
        self.store.set('group_rules', {'100001': {'daily_push_days': 7}})
        self.store.set('rule_presets', [{'rules': {'daily_push_days': 7}}])
        loaded = main.DataStore(self.path)
        self.assertEqual(loaded.get('fetch_ahead_days'), 3)
        self.assertEqual(loaded.get('daily_push_days'), 3)
        self.assertEqual(loaded.get('group_rules')['100001']['daily_push_days'], 3)
        self.assertEqual(loaded.get('rule_presets')[0]['rules']['daily_push_days'], 3)

    def test_new_default_and_validation(self):
        self.assertEqual(main.DataStore._DEFAULTS['fetch_ahead_days'], 3)
        for key in ('fetch_ahead_days', 'daily_push_days'):
            self.assertEqual(config.validate_config({key: 3})[key], 3)
            with self.assertRaises(config.ConfigError):
                config.validate_config({key: 4})
        with self.assertRaises(config.ConfigError):
            config.validate_config({'fetch_ahead_hours': 73})
        with self.assertRaises(config.ConfigError):
            ai._window('', 4, results=False)
        start, end = ai._window('', 30, results=True)
        self.assertEqual((end - start).days, 30)


class ScheduleLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_rejects_four_days_before_query(self):
        plugin = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        plugin._schedule_query_result = AsyncMock()
        plugin._query_reply = AsyncMock()
        event = types.SimpleNamespace(get_platform_name=lambda: 'aiocqhttp')
        await plugin.cmd_cs_three_days(event, '4')
        plugin._schedule_query_result.assert_not_awaited()
        self.assertIn('1–3', plugin._query_reply.call_args.args[1])

    async def test_preview_rejects_four_days(self):
        plugin = types.SimpleNamespace(_token='placeholder', store=None)
        # A minimal local store; no real provider or message target.
        with tempfile.TemporaryDirectory() as directory:
            plugin.store = main.DataStore(str(Path(directory) / 'state.json'))
            with self.assertRaises(config.ConfigError):
                await previews.build_preview(plugin, {'days': 4})
