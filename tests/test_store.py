import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import load_module


store_module = load_module("store")
DataStore = store_module.DataStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.store = DataStore(str(self.path))

    def write_config(self, config):
        self.path.write_text(json.dumps(config), encoding="utf-8")
        return DataStore(str(self.path))

    def test_mutable_defaults_are_isolated_between_instances(self):
        self.store.follow_team(1, "NAVI")
        self.store.set_match_snapshot(1, "date", "a", "b")
        another = DataStore(str(Path(self.temp.name) / "another.json"))
        self.assertEqual(another.get_followed_teams(), [])
        self.assertIsNone(another.get_match_snapshot(1))
        self.assertEqual(DataStore._DEFAULTS["followed_teams"], [])

    def test_loading_missing_fields_does_not_share_defaults(self):
        first = self.write_config({"pandascore_token": "existing"})
        second = DataStore(str(self.path))
        first.add_group("1")
        self.assertEqual(second.get_groups(), [])

    def test_relative_filename_can_be_created(self):
        old_cwd = os.getcwd()
        try:
            os.chdir(self.temp.name)
            DataStore("relative.json").save()
            self.assertTrue(Path("relative.json").is_file())
        finally:
            os.chdir(old_cwd)

    def test_non_object_config_is_backed_up_before_recovery(self):
        recovered = self.write_config([])
        self.assertEqual(recovered.get_groups(), [])
        backups = list(self.path.parent.glob("config.json.invalid-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), [])

    def test_wrong_container_shapes_recover_and_legacy_hours_migrate(self):
        recovered = self.write_config({"followed_teams": None, "match_snapshots": [], "fetch_ahead_hours": 49})
        self.assertEqual(recovered.get_followed_teams(), [])
        self.assertIsNone(recovered.get_match_snapshot(1))
        self.assertEqual(recovered.get("fetch_ahead_days"), 3)

    def test_serialization_failure_leaves_existing_file_and_no_temp_files(self):
        self.store.save()
        previous = self.path.read_bytes()
        self.store._data["bad_value"] = object()
        with self.assertRaises(TypeError):
            self.store.save()
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_replace_failure_leaves_existing_file_and_no_temp_files(self):
        self.store.save()
        previous = self.path.read_bytes()
        with patch.object(store_module.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.set("remind_minutes", 20)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_unchanged_schedule_and_snapshot_do_not_write(self):
        self.store.set_match_schedule(1, "date")
        self.store.set_match_snapshot(1, "date", "a", "b")
        with patch.object(self.store, "save") as save:
            self.store.set_match_schedule(1, "date")
            self.store.set_match_snapshot(1, "date", "a", "b")
            self.store.del_match_snapshot(2)
            save.assert_not_called()

    def test_export_is_detached_and_excludes_secrets_and_runtime_state(self):
        self.store.follow_team(1, "NAVI")
        self.store.set("pandascore_token", "secret")
        self.store.mark_delivery_target("upcoming:1", "group:123")
        exported = self.store.export_all()
        exported["followed_teams"][0]["name"] = "changed"
        self.assertEqual(self.store.get_followed_team_names(), ["NAVI"])
        for key in ("pandascore_token", "web_panel_token", "match_snapshots", "delivery_receipts"):
            self.assertNotIn(key, exported)

    def test_import_rejects_invalid_input_without_partial_changes(self):
        self.store.save()
        previous = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.import_config({"remind_minutes": 20, "push_groups": "invalid"})
        self.assertEqual(self.store.get_remind_minutes(), 10)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_import_copies_values_and_preserves_runtime_state(self):
        self.store.mark_upcoming_notified(1)
        cfg = {"push_groups": ["123"], "notified_upcoming": []}
        self.store.import_config(cfg)
        cfg["push_groups"].append("456")
        self.assertEqual(self.store.get_groups(), ["123"])
        self.assertTrue(self.store.is_upcoming_notified(1))

    def test_import_save_failure_rolls_back_memory(self):
        with patch.object(self.store, "save", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.import_config({"remind_minutes": 20})
        self.assertEqual(self.store.get_remind_minutes(), 10)

    def test_legacy_team_can_be_removed_without_removing_id_follow(self):
        self.store.import_config({"followed_teams": ["NAVI", {"id": 1, "name": "NAVI"}]})
        self.assertTrue(self.store.unfollow_team_by_name("NAVI"))
        self.assertFalse(self.store.unfollow_team_by_name("NAVI"))
        self.assertEqual(self.store.get_followed_team_ids(), {1})

    def test_delivery_receipts_persist_and_do_not_duplicate_targets(self):
        self.store.mark_delivery_target("upcoming:1", "group:123")
        with patch.object(self.store, "save") as save:
            self.store.mark_delivery_target("upcoming:1", "group:123")
            save.assert_not_called()
        reloaded = DataStore(str(self.path))
        self.assertEqual(reloaded.get_delivery_targets("upcoming:1"), {"group:123"})
        reloaded.clear_upcoming_notified(1)
        self.assertEqual(reloaded.get_delivery_targets("upcoming:1"), set())

    def test_clear_match_history_preserves_tournament_receipts(self):
        for key in ("upcoming:1", "finished:1", "reschedule:1:date", "tournament:1"):
            self.store.mark_delivery_target(key, "group:123")
        self.store.clear_match_notifications()
        self.assertEqual(self.store.get_delivery_targets("upcoming:1"), set())
        self.assertEqual(self.store.get_delivery_targets("finished:1"), set())
        self.assertEqual(self.store.get_delivery_targets("reschedule:1:date"), set())
        self.assertEqual(self.store.get_delivery_targets("tournament:1"), {"group:123"})

    def test_receipts_prune_with_completed_notification_history(self):
        self.store._data["notified_upcoming"] = list(range(500))
        self.store.mark_delivery_target("upcoming:0", "group:123")
        self.store.mark_delivery_target("reschedule:0:date", "group:123")
        self.store.mark_delivery_target("upcoming:500", "group:123")
        self.store.mark_upcoming_notified(500)
        self.assertEqual(self.store.get_delivery_targets("upcoming:0"), set())
        self.assertEqual(self.store.get_delivery_targets("reschedule:0:date"), set())
        self.assertEqual(self.store.get_delivery_targets("upcoming:500"), {"group:123"})


if __name__ == "__main__":
    unittest.main()
