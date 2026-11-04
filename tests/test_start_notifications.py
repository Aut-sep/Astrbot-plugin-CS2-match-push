"""Offline regression checks; no AstrBot service, API requests or QQ messages."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def host_module(name):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


# Substitute the host API only. Import the actual plugin and persistence code.
host_module("astrbot")
host_module("astrbot.api").logger = logging.getLogger("offline-start-check")
star = host_module("astrbot.api.star")
star.Context = star.Star = star.StarTools = object
star.register = lambda *args, **kwargs: lambda function: function
event = host_module("astrbot.api.event")
event.AstrMessageEvent = event.MessageEventResult = object
event.filter = types.SimpleNamespace(
    **{name: star.register for name in (
        "permission_type", "event_message_type", "platform_adapter_type", "command", "regex", "llm_tool", "on_llm_request",
    )},
    PermissionType=types.SimpleNamespace(ADMIN=1),
    EventMessageType=types.SimpleNamespace(PRIVATE_MESSAGE=1),
    PlatformAdapterType=types.SimpleNamespace(QQOFFICIAL=1, QQOFFICIAL_WEBHOOK=2),
)
package = host_module("cs_start_checked")
package.__path__ = [str(ROOT)]
main = importlib.import_module("cs_start_checked.main")
fmt = importlib.import_module("cs_start_checked.formatters")
NOW = datetime(2026, 11, 2, 10, 17, 54, tzinfo=timezone.utc)
MATCH = {
    "id": 123, "status": "running", "scheduled_at": "2026-11-02T10:20:00Z",
    "begin_at": "2026-11-02T10:17:39Z", "number_of_games": 3,
    "league": {"name": "Test League"}, "tournament": {"tier": "s"},
    "opponents": [{"opponent": {"id": 1, "name": "TYLOO"}},
                  {"opponent": {"id": 2, "name": "MOUZ"}}],
}


class StartNoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "state.json")
        self.plugin = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        self.plugin.store = main.DataStore(self.path)
        self.plugin.store.set("push_groups", ["100001", "100002"])
        self.plugin.panel = types.SimpleNamespace(push_log=lambda *args: None)
        self.plugin.context = object()
        self.plugin._result_meta = {}
        self.plugin._result_tasks = {}
        self.plugin._match_with_snapshot_names = deepcopy
        self.plugin._format = lambda function, *args: function(*args)
        self.plugin._build_message_chain = lambda components: components
        self.plugin._notification_components = AsyncMock(return_value=["offline card"])
        self.sent = []

        async def send(context, target_type, target_id, chain, **kwargs):
            self.sent.append(target_id)
            return {"message_id": len(self.sent)}

        self.sender = patch.object(main, "send_target", side_effect=send)
        self.sender.start()
        self.addCleanup(self.sender.stop)
        self.time = patch.object(main, "now_utc", return_value=NOW)
        self.time.start()
        self.addCleanup(self.time.stop)

    async def test_early_start_notifies_once_and_keeps_result_tracking(self):
        plugin = self.plugin
        finished = {**MATCH, "status": "finished"}
        plugin.client = types.SimpleNamespace(get_match_result=AsyncMock(
            side_effect=[deepcopy(MATCH), deepcopy(MATCH), finished]))
        plugin._push_finished = AsyncMock(return_value=True)
        with patch.object(main.asyncio, "sleep", new=AsyncMock()):
            await plugin._run_result_poll(deepcopy(MATCH))
        self.assertEqual(self.sent, ["100001", "100002"])
        self.assertTrue(plugin.store.is_upcoming_notified(123))
        self.assertTrue(plugin.store.is_finished_notified(123))
        # Each group now renders its own result under its effective rules.
        self.assertEqual(plugin._push_finished.await_count, 2)
        text = plugin._notification_components.call_args.args[0]
        self.assertIn("已开赛", text)
        self.assertIn("18:17", text)
        self.assertNotIn("18:20", text)
        self.assertTrue(plugin._notification_components.call_args.kwargs["started_notice"])

    async def test_partial_reminder_and_failed_fallback_survive_reload(self):
        plugin = self.plugin
        plugin.store.mark_delivery_target("upcoming:123", "group:100001")
        with patch.object(main, "send_target", new=AsyncMock(side_effect=RuntimeError("offline failure"))):
            self.assertFalse(await plugin._maybe_push_started_notice(deepcopy(MATCH)))
        self.assertFalse(plugin.store.is_upcoming_notified(123))
        plugin.store = main.DataStore(self.path)
        self.assertTrue(await plugin._maybe_push_started_notice(deepcopy(MATCH)))
        self.assertEqual(self.sent, ["100002"])
        plugin.store = main.DataStore(self.path)
        self.assertFalse(await plugin._maybe_push_started_notice(deepcopy(MATCH)))
        self.assertEqual(self.sent, ["100002"])

    async def test_terminal_states_and_saved_filters_do_not_backfill(self):
        for status in ("not_started", "finished", "canceled", "postponed"):
            with self.subTest(status=status):
                self.assertFalse(await self.plugin._maybe_push_started_notice({**MATCH, "status": status}))
        for key, value in (
            ("notified_upcoming", [123]), ("notified_finished", [123]),
            ("result_recovery_ignored", {"123": "skip"}),
            ("blacklist_teams", ["tyloo"]), ("blacklist_leagues", ["test"]),
            ("min_tiers", ["b"]),
        ):
            original = deepcopy(self.plugin.store.get(key))
            self.plugin.store.set(key, value)
            with self.subTest(key=key):
                self.assertFalse(await self.plugin._maybe_push_started_notice(deepcopy(MATCH)))
            self.plugin.store.set(key, original)
        self.assertEqual(self.sent, [])

    async def test_reload_restores_known_early_start_before_planned_time(self):
        plugin = self.plugin
        plugin._token = "offline-placeholder"
        plugin.store.save_pending_result(deepcopy(MATCH), (NOW + timedelta(hours=6)).isoformat())
        plugin.store = main.DataStore(self.path)
        restored = []

        def capture(coroutine, name):
            restored.append(name)
            coroutine.close()
            return types.SimpleNamespace(done=lambda: False)

        plugin._create_background_task = capture
        plugin._restore_result_polls()
        self.assertEqual(restored, ["result:123"])

    async def test_started_text_and_card_use_actual_time_or_unknown(self):
        plugin = self.plugin
        plugin._resolve_team_marks = AsyncMock(return_value=[])
        plugin._resolve_event_marks = AsyncMock(return_value=[])
        plugin._notice_teams = lambda **kwargs: []
        plugin._message_component_types = lambda: (None, None, object)
        plugin._make_image_file_component = lambda image_type, path: path
        plugin._banner_renderer = types.SimpleNamespace(render_notification=lambda *args, **kwargs: kwargs)
        for begin, expected in ((MATCH["begin_at"], "18:17"), (None, ""), ("invalid", "")):
            match = {**MATCH, "begin_at": begin}
            text = fmt.fmt_started(match)
            components = await main.CSMatchPlugin._notification_components(
                plugin, text, match=match, output_path="offline-only", started_notice=True)
            self.assertEqual(components[0]["start_time"], expected)
            self.assertNotIn("18:20", text)
            if not expected:
                self.assertIn("开赛时间待确认", text)


if __name__ == "__main__":
    unittest.main()
