from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from tests.helpers import load_module

DataStore = load_module("store").DataStore
WebPanel = load_module("web_panel").WebPanel


def plugin_for(store):
    return SimpleNamespace(
        store=store, reload_runtime_config=AsyncMock(), apply_web_panel_config=AsyncMock(),
        rebuild_all_match_tasks=Mock(), rebuild_tournament_tasks=Mock(),
        schedule_refresh=Mock(), has_daily_matches_to_push=Mock(return_value=False),
        schedule_instant_push=Mock(), update_match_remind=Mock(return_value="updated"),
        is_test_mode_enabled=Mock(return_value=True), has_test_target=Mock(return_value=True),
        run_test_push=AsyncMock(return_value="sent"),
    )


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.store = DataStore(str(Path(self.temp.name) / "config.json"))
        self.plugin = plugin_for(self.store)
        self.panel = WebPanel(self.plugin)
        self.client = TestClient(TestServer(self.panel.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_invalid_payloads_return_json_400_without_mutation(self):
        old = self.store.get("remind_minutes")
        cases = [
            ("PATCH", "/api/config", []),
            ("POST", "/api/config/import", {"remind_minutes": "bad"}),
            ("PATCH", "/api/config", {"remind_minutes": 15, "notify_all_followed": "false"}),
            ("POST", "/api/groups", {"gid": True}),
            ("POST", "/api/teams", {"id": "bad", "name": "Team"}),
            ("POST", "/api/teams", {"id": 1, "name": []}),
            ("POST", "/api/teams", {"id": 1, "name": "Team", "slug": []}),
            ("POST", "/api/teams/unfollow", []),
            ("POST", "/api/push_now", {"days": -1}),
            ("POST", "/api/test", {"action": []}),
            ("PATCH", "/api/matches/1", {"remind_minutes": "bad"}),
            ("PATCH", "/api/matches/1", {"remind_minutes": 10.5}),
            ("PATCH", "/api/matches/-1", {"remind_minutes": 10}),
        ]
        for method, path, body in cases:
            with self.subTest(method=method, path=path, body=body):
                response = await self.client.request(method, path, json=body)
                self.assertEqual(response.status, 400)
                self.assertFalse((await response.json())["ok"])
        self.assertEqual(self.store.get("remind_minutes"), old)
        self.plugin.reload_runtime_config.assert_not_called()

    async def test_invalid_json_is_not_silently_treated_as_an_empty_command(self):
        for path in ("/api/groups", "/api/push_now", "/api/test", "/api/config/import"):
            with self.subTest(path=path):
                response = await self.client.post(path, data="{bad", headers={"Content-Type": "application/json"})
                self.assertEqual(response.status, 400)
                self.assertFalse((await response.json())["ok"])
        self.plugin.schedule_instant_push.assert_not_called()
        self.plugin.run_test_push.assert_not_called()

    async def test_reminder_and_filters_rebuild_existing_tasks(self):
        response = await self.client.patch("/api/config", json={"remind_minutes": 15})
        self.assertTrue((await response.json())["ok"])
        self.assertEqual(self.store.get_remind_minutes(), 15)
        self.plugin.rebuild_all_match_tasks.assert_called_once()
        self.plugin.schedule_refresh.assert_not_called()
        self.plugin.rebuild_all_match_tasks.reset_mock()
        response = await self.client.patch("/api/config", json={"blacklist_teams": ["Team"]})
        self.assertTrue((await response.json())["ok"])
        self.plugin.rebuild_all_match_tasks.assert_called_once()
        self.plugin.rebuild_tournament_tasks.assert_called_once()

    async def test_unchanged_config_does_not_restart_tasks_or_client(self):
        response = await self.client.patch("/api/config", json={"remind_minutes": 10})
        self.assertTrue((await response.json())["ok"])
        self.plugin.rebuild_all_match_tasks.assert_not_called()
        self.plugin.reload_runtime_config.assert_not_called()

    async def test_failed_web_change_rolls_back_all_settings(self):
        self.plugin.apply_web_panel_config.side_effect = OSError("address in use")
        response = await self.client.patch("/api/config", json={"web_panel_port": 9000, "remind_minutes": 20})
        self.assertEqual(response.status, 500)
        self.assertEqual(self.store.get("web_panel_port"), 8765)
        self.assertEqual(self.store.get("remind_minutes"), 10)
        self.plugin.reload_runtime_config.assert_not_called()
        self.plugin.rebuild_all_match_tasks.assert_not_called()

    async def test_unfollow_legacy_name(self):
        self.store.import_config({"followed_teams": ["Legacy", {"id": 5, "name": "Exact"}]})
        response = await self.client.post("/api/teams/unfollow", json={"name": "Legacy"})
        self.assertTrue((await response.json())["ok"])
        self.assertEqual(self.store.get_followed_team_ids(), {5})
        self.assertEqual(self.store.get_followed_team_names(), ["Exact"])

    async def test_custom_reminder_can_be_reset_to_global(self):
        response = await self.client.patch("/api/matches/5", json={"remind_minutes": None})
        self.assertTrue((await response.json())["ok"])
        self.plugin.update_match_remind.assert_called_once_with(5, None)

    async def test_local_browser_cross_origin_mutations_require_token(self):
        response = await self.client.patch("/api/config", json={"remind_minutes": 20}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.store.get("remind_minutes"), 10)
        self.store.set("web_panel_token", "secret")
        response = await self.client.patch("/api/config", json={"remind_minutes": 20}, headers={"Origin": "http://localhost:9001", "Cookie": "cs_panel_token=secret"})
        self.assertEqual(response.status, 403)
        response = await self.client.patch("/api/config", json={"remind_minutes": 20}, headers={"Origin": "https://evil.example", "Authorization": "Bearer secret"})
        self.assertEqual(response.status, 200)


class WebListenerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.store = DataStore(str(Path(self.temp.name) / "config.json"))
        self.plugin = plugin_for(self.store)
        self.panel = WebPanel(self.plugin)
        self.cleanups = 0

        async def cleanup(app):
            self.cleanups += 1

        self.panel.app.on_cleanup.append(cleanup)

        async def apply():
            return await self.panel.reconfigure(self.store.get("web_panel_enabled"), self.store.get("web_panel_host"), self.store.get("web_panel_port"))

        self.plugin.apply_web_panel_config.side_effect = apply
        await self.panel.start("127.0.0.1", 0)
        self.port = self.panel._site._server.sockets[0].getsockname()[1]
        self.store.set("web_panel_port", self.port)
        self.session = ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        await self.panel.stop()
        self.temp.cleanup()

    async def test_response_survives_port_change_and_disable_then_reenable(self):
        runner = self.panel._runner
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            new_port = available.getsockname()[1]
        async with self.session.patch(f"http://127.0.0.1:{self.port}/api/config", json={"web_panel_port": new_port}) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["web_reconfigured"])
        self.assertIs(self.panel._runner, runner)
        self.assertEqual(self.cleanups, 0)
        async with self.session.get(f"http://127.0.0.1:{new_port}/api/config") as response:
            self.assertEqual(response.status, 200)
        async with self.session.patch(f"http://127.0.0.1:{new_port}/api/config", json={"web_panel_enabled": False}) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue((await response.json())["web_reconfigured"])
        self.assertEqual(self.cleanups, 0)
        self.assertIs(self.panel._runner, runner)
        await self.panel.reconfigure(True, "127.0.0.1", new_port)
        async with self.session.get(f"http://127.0.0.1:{new_port}/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn('<html lang="zh-CN">', await response.text())
        await self.panel.stop()
        self.assertEqual(self.cleanups, 1)

    async def test_failed_bind_keeps_previous_listener_and_config(self):
        old_site = self.panel._site
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            busy_port = occupied.getsockname()[1]
            async with self.session.patch(f"http://127.0.0.1:{self.port}/api/config", json={"web_panel_port": busy_port}) as response:
                self.assertEqual(response.status, 500)
                self.assertFalse((await response.json())["ok"])
        self.assertIs(self.panel._site, old_site)
        self.assertEqual(self.store.get("web_panel_port"), self.port)
        self.assertEqual(self.cleanups, 0)
        async with self.session.get(f"http://127.0.0.1:{self.port}/api/config") as response:
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
