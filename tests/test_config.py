import unittest

from tests.helpers import load_module

config = load_module("config")


class ConfigTests(unittest.TestCase):
    def test_normalizes_legacy_export_without_importing_runtime_state(self):
        result = config.validate_config({
            "fetch_ahead_hours": "25", "remind_minutes": "15",
            "followed_teams": ["Legacy", {"id": None, "name": "Old"},
                               {"id": "5", "name": "Team", "slug": "team"}],
            "push_groups": [123, "123", "456"],
            "min_tiers": ["S", "a", "s"],
            "daily_push_times": ["12:00", "08:00", "12:00"],
            "match_snapshots": {"4": {"sched": "invalid"}},
            "notified_finished": [1], "unknown": True,
        })
        self.assertEqual(result["fetch_ahead_days"], 2)
        self.assertEqual(result["remind_minutes"], 15)
        self.assertEqual(result["followed_teams"][:2], ["Legacy", "Old"])
        self.assertEqual(result["followed_teams"][2]["id"], 5)
        self.assertEqual(result["push_groups"], ["123", "456"])
        self.assertEqual(result["min_tiers"], ["s", "a"])
        self.assertEqual(result["daily_push_times"], ["08:00", "12:00"])
        self.assertNotIn("match_snapshots", result)
        self.assertNotIn("notified_finished", result)

    def test_rejects_invalid_types_and_bounds(self):
        invalid = [
            [], None, {"remind_minutes": 0}, {"remind_minutes": 121},
            {"remind_minutes": True}, {"remind_minutes": 10.5},
            {"web_panel_port": 65536}, {"web_panel_port": "NaN"},
            {"web_panel_host": "http://localhost/"},
            {"notify_all_followed": "false"}, {"daily_push_times": ["24:00"]},
            {"daily_push_times": ["8:00"]}, {"min_tiers": "s"},
            {"min_tiers": ["z"]}, {"blacklist_teams": [4]},
            {"followed_teams": [{"id": "bad", "name": "Team"}]},
            {"followed_teams": [{"id": 1, "name": 2}]},
            {"custom_streams": [{}]}, {"custom_remind_minutes": {"5": None}},
            {"custom_streams": [{"name": "Stream", "url": "javascript:alert(1)"}]},
            {"custom_remind_minutes": {"bad": 10}}, {"test_target_id": "abc"},
            {"test_target_type": "room"}, {"push_groups": [True]},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(config.ConfigError):
                config.validate_config(value)

    def test_accepts_ipv6_and_empty_optional_lists(self):
        self.assertEqual(config.validate_config({
            "web_panel_host": "::1", "daily_push_times": [], "min_tiers": [],
            "test_target_id": "", "custom_remind_minutes": {"5": "20"},
        }), {
            "web_panel_host": "::1", "daily_push_times": [], "min_tiers": [],
            "test_target_id": "", "custom_remind_minutes": {"5": 20},
        })


if __name__ == "__main__":
    unittest.main()
