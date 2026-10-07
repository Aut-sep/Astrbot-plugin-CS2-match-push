import asyncio
from datetime import timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.helpers import load_module

main = load_module("main")
store_module = load_module("store")


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugin = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        p = self.plugin
        p.context = SimpleNamespace()
        p.store = store_module.DataStore(str(Path(self.tmp.name) / "data.json"))
        p.panel = SimpleNamespace(push_log=Mock())
        p.client = SimpleNamespace(get_upcoming_matches=AsyncMock(), get_match_result=AsyncMock(), headers={})
        p._token = "test"
        p._fetch_interval = 10
        p._fetch_ahead = 2
        p._web_host = "127.0.0.1"
        p._web_port = 8765
        p._scheduled = []
        p._match_tasks = {}
        p._scheduled_mids = set()
        p._result_tasks = {}
        p._result_meta = {}
        p._tournament_tasks = {}
        p._background_tasks = set()
        p._notified_reschedule = {}
        p._pending_reschedules = {}
        p._fetch_schedule_lock = asyncio.Lock()
        p._refresh_requested = False
        self.now = main.now_utc()
        self.match = {"id": 42, "scheduled_at": (self.now + timedelta(minutes=2)).isoformat(),
                      "tournament": {"tier": "s"}, "opponents": [
                          {"opponent": {"id": 1, "name": "One"}},
                          {"opponent": {"id": 2, "name": "Two"}}]}

    async def asyncTearDown(self):
        tasks = set(self.plugin._background_tasks) | set(self.plugin._match_tasks.values()) | set(self.plugin._result_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_late_fetch_sends_reminder_before_start(self):
        p = self.plugin
        p._push_upcoming = AsyncMock(return_value=True)
        with patch.object(main, "now_utc", return_value=self.now), patch.object(main.asyncio, "sleep", new=AsyncMock()):
            await p._run_remind(self.match, 10)
        p._push_upcoming.assert_awaited_once()
        self.assertTrue(p.store.is_upcoming_notified(42))

    async def test_failed_reminder_is_retried_then_marked(self):
        p = self.plugin
        async def send(*args):
            self.assertFalse(p.store.is_upcoming_notified(42))
            return p._push_upcoming.await_count > 1
        p._push_upcoming = AsyncMock(side_effect=send)
        with patch.object(main, "now_utc", return_value=self.now), patch.object(main.asyncio, "sleep", new=AsyncMock()):
            await p._run_remind(self.match, 10)
        self.assertEqual(p._push_upcoming.await_count, 2)
        self.assertTrue(p.store.is_upcoming_notified(42))

    async def test_started_match_does_not_send_late_reminder(self):
        p = self.plugin
        p._push_upcoming = AsyncMock(return_value=True)
        self.match["scheduled_at"] = (self.now - timedelta(seconds=1)).isoformat()
        await p._run_remind(self.match, 10)
        p._push_upcoming.assert_not_awaited()

    async def test_old_reminder_cleanup_preserves_replacement(self):
        p = self.plugin
        entered = asyncio.Event()
        async def wait(*args):
            entered.set()
            await asyncio.Event().wait()
        p._run_remind = wait
        old = p._start_match_remind_task(self.match, 10)
        await entered.wait()
        old.cancel()
        replacement = p._start_match_remind_task(self.match, 20)
        await asyncio.gather(old, return_exceptions=True)
        self.assertIs(p._match_tasks[42], replacement)
        self.assertIn(42, p._scheduled_mids)
        self.assertNotIn(42, p._result_tasks)

    async def test_old_result_cleanup_preserves_replacement(self):
        p = self.plugin
        entered = asyncio.Event()
        async def fetch(*args):
            entered.set()
            await asyncio.Event().wait()
        p.client.get_match_result.side_effect = fetch
        old = asyncio.create_task(p._run_result_poll(self.match))
        p._result_tasks[42] = old
        await entered.wait()
        old.cancel()
        replacement = asyncio.create_task(asyncio.Event().wait())
        p._result_tasks[42] = replacement
        meta = p._result_meta[42]
        await asyncio.gather(old, return_exceptions=True)
        self.assertIs(p._result_tasks[42], replacement)
        self.assertIs(p._result_meta[42], meta)

    async def test_result_failure_does_not_mark_and_retries(self):
        p = self.plugin
        p.client.get_match_result.return_value = {**self.match, "status": "finished"}
        async def send(*args):
            self.assertFalse(p.store.is_finished_notified(42))
            return p._push_finished.await_count > 1
        p._push_finished = AsyncMock(side_effect=send)
        task = asyncio.create_task(p._run_result_poll(self.match))
        p._result_tasks[42] = task
        with patch.object(main.asyncio, "sleep", new=AsyncMock()):
            await task
        self.assertEqual(p._push_finished.await_count, 2)
        self.assertTrue(p.store.is_finished_notified(42))
        self.assertNotIn(42, p._result_tasks)

    async def test_canceled_match_stops_polling(self):
        p = self.plugin
        p.client.get_match_result.return_value = {"status": "canceled"}
        task = asyncio.create_task(p._run_result_poll(self.match))
        p._result_tasks[42] = task
        await task
        p.client.get_match_result.assert_awaited_once()
        self.assertFalse(p.store.is_finished_notified(42))

    async def test_empty_schedule_removes_stale_reminders(self):
        p = self.plugin
        p.client.get_upcoming_matches.return_value = []
        task = asyncio.create_task(asyncio.Event().wait())
        p._match_tasks[42] = task
        p._scheduled_mids.add(42)
        p._scheduled = [self.match]
        await p._fetch_and_schedule()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(p._scheduled, [])
        self.assertEqual(p._match_tasks, {})
        self.assertTrue(task.cancelled())

    async def test_failed_schedule_preserves_tasks(self):
        p = self.plugin
        p.client.get_upcoming_matches.return_value = None
        p._scheduled = [self.match]
        await p._fetch_and_schedule()
        self.assertEqual(p._scheduled, [self.match])

    async def test_refresh_during_fetch_gets_another_pass(self):
        p = self.plugin
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def refresh():
            nonlocal count
            count += 1
            if count == 1:
                entered.set()
                await release.wait()
        p._fetch_and_schedule_locked = refresh
        task = asyncio.create_task(p._fetch_and_schedule())
        await entered.wait()
        await p._fetch_and_schedule()
        release.set()
        await task
        self.assertEqual(count, 2)

    async def test_partial_delivery_only_retries_failed_target(self):
        p = self.plugin
        p.store.import_config({"push_groups": ["111", "222"]})
        p._build_message_chain = Mock(return_value="chain")
        attempts = []
        async def send(**kwargs):
            gid = kwargs["id"]
            attempts.append(gid)
            if gid == "222" and attempts.count(gid) == 1:
                raise RuntimeError("offline")
            return None  # AstrBot can return no message reference on success.
        with patch.object(main.StarTools, "send_message_by_id", new=send, create=True):
            self.assertFalse(await p._deliver_notification("upcoming:42", lambda: p._push_components([])))
            self.assertTrue(await p._deliver_notification("upcoming:42", lambda: p._push_components([])))
        self.assertEqual(attempts, ["111", "222", "222"])
        reloaded = store_module.DataStore(p.store.path)
        self.assertEqual(reloaded.get_delivery_targets("upcoming:42"), {"group:111", "group:222"})

    async def test_reload_reuses_client_and_can_clear_token(self):
        p = self.plugin
        client = p.client
        p.store.set("pandascore_token", "")
        await p.reload_runtime_config()
        self.assertIs(p.client, client)
        self.assertEqual(p._token, "")
        self.assertEqual(client.headers["Authorization"], "Bearer ")

    async def test_current_astrbot_send_result_is_respected(self):
        p = self.plugin
        p.store.import_config({"push_groups": ["111"]})
        p._build_message_chain = Mock(return_value="chain")
        platform = SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp", id="qq-main"))
        p.context.platform_manager = SimpleNamespace(get_insts=lambda: [platform])
        p.context.send_message = AsyncMock(side_effect=[False, True])
        self.assertFalse(await p._deliver_notification("upcoming:42", lambda: p._push_components([])))
        self.assertEqual(p.store.get_delivery_targets("upcoming:42"), set())
        self.assertTrue(await p._deliver_notification("upcoming:42", lambda: p._push_components([])))
        p.context.send_message.assert_awaited_with("qq-main:GroupMessage:111", "chain")

    async def test_test_push_reports_send_failure(self):
        p = self.plugin
        p.store.import_config({"test_target_type": "private", "test_target_id": "123"})
        p._push_components_to_targets_with_refs = AsyncMock(return_value=[])
        with self.assertRaises(main.DeliveryError):
            await p._push_test_components([])

    async def test_team_id_follow_does_not_match_different_team_with_same_name(self):
        p = self.plugin
        p.store.import_config({"followed_teams": [{"id": 99, "name": "One"}], "min_tiers": []})
        p.client.get_upcoming_matches.return_value = [self.match]
        await p._fetch_and_schedule()
        self.assertEqual(p._scheduled, [])
        p.store.import_config({"followed_teams": ["One"]})
        p._start_match_remind_task = Mock()
        await p._fetch_and_schedule()
        self.assertEqual(p._scheduled, [self.match])

    async def test_panel_url_formats_ipv6_and_encodes_token(self):
        p = self.plugin
        p.store.import_config({"web_panel_host": "::", "web_panel_token": "token&特殊"})
        from urllib.parse import parse_qs, urlparse
        url = urlparse(p._web_panel_url(include_token=True))
        self.assertEqual(url.hostname, "::")
        self.assertEqual(parse_qs(url.query)["token"], ["token&特殊"])

    async def test_reschedule_failure_retries_after_snapshot_updated(self):
        p = self.plugin
        old_time = (self.now + timedelta(minutes=30)).isoformat()
        p.store.set_match_snapshot(42, old_time, "One", "Two")
        p.client.get_upcoming_matches.return_value = [self.match]
        p._start_match_remind_task = Mock()
        p._push = AsyncMock(side_effect=[False, True])
        await p._fetch_and_schedule()
        self.assertIn(42, p._pending_reschedules)
        self.assertEqual(p.store.get_match_snapshot(42)["sched"], self.match["scheduled_at"])
        await p._fetch_and_schedule()
        self.assertNotIn(42, p._pending_reschedules)
        self.assertEqual(p._push.await_count, 2)

    async def test_panel_handles_result_task_before_first_poll(self):
        p = self.plugin
        task = asyncio.create_task(asyncio.Event().wait())
        p._result_tasks[42] = task
        rows = p.get_panel_matches()
        self.assertEqual(rows[0]["id"], 42)
        self.assertEqual(rows[0]["_task_status"], "polling_result")

    async def test_first_install_keeps_astrbot_config_and_disabled_panel(self):
        root = Path(self.tmp.name) / "plugin"
        root.mkdir()
        cfg = {"pandascore_token": "configured", "fetch_interval_min": 30,
               "fetch_ahead_hours": 72, "web_panel_port": 9999,
               "web_panel_enabled": False, "image_push_enabled": False}
        with patch.object(main.StarTools, "get_data_dir", return_value=root, create=True):
            plugin = main.CSMatchPlugin(SimpleNamespace(), cfg)
        self.assertEqual(plugin._token, "configured")
        self.assertEqual(plugin._fetch_interval, 30)
        self.assertEqual(plugin._fetch_ahead, 3)
        self.assertEqual(plugin._web_port, 9999)
        self.assertFalse(plugin.store.get("web_panel_enabled"))
        self.assertFalse(plugin.store.get("image_push_enabled"))
        await plugin.client.close()
