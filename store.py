"""
store.py — 数据持久化层
负责所有配置、状态的读取与写入（JSON 文件）
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from typing import Optional

from astrbot.api import logger


class DataStore:
    _DEPRECATED_KEYS = (
        "notified_round_summaries",
        "round_summary_enabled",
        "round_gap_hours",
    )

    _DEFAULTS = {
        "pandascore_token":           "",
        "followed_teams":              [],
        "push_groups":                 [],
        "remind_minutes":              10,
        "min_tiers":                   ["s", "a"],
        "notified_upcoming":           [],
        "notified_finished":           [],
        "notified_tournaments":        [],
        "match_schedules":             {},
        "match_snapshots":             {},  # mid -> {sched, t1, t2} 用于检测 TBD 变更
        "custom_remind_minutes":       {},  # mid -> 自定义提醒分钟数（覆盖全局）
        "web_panel_host":              "127.0.0.1",
        "reschedule_notify":           True,
        "web_panel_port":              8765,
        "web_panel_enabled":           True,
        "web_panel_token":             "",
        "fetch_interval_min":          10,
        "fetch_ahead_days":            2,
        "custom_streams":              [],
        "blacklist_teams":             [],
        "blacklist_leagues":           [],
        "notify_all_followed":         True,
        "daily_push_enabled":          False,
        "daily_push_times":            ["08:00"],
        "daily_push_days":             1,
        "tournament_announce_enabled": True,
        "tournament_announce_hours":   2,
        "image_push_enabled":          True,
        "test_mode_enabled":           False,
        "test_target_type":            "private",
        "test_target_id":              "",
    }

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data = self._load()

    @staticmethod
    def _hours_to_days(hours, default: int = 2) -> int:
        try:
            return max(1, (int(hours) + 23) // 24)
        except (TypeError, ValueError):
            return default

    def _backup_corrupt_file(self):
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = f"{self.path}.invalid-{ts}"
        try:
            shutil.copy2(self.path, backup_path)
            logger.warning(f"[CS] 配置 JSON 无法解析，已备份到 {backup_path}")
        except OSError as e:
            logger.error(f"[CS] 配置备份失败: {e}")

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "fetch_ahead_days" not in data and "fetch_ahead_hours" in data:
                    data["fetch_ahead_days"] = self._hours_to_days(data.get("fetch_ahead_hours"))
                for key in self._DEPRECATED_KEYS:
                    data.pop(key, None)
                for k, v in self._DEFAULTS.items():
                    data.setdefault(k, v)
                return data
            except json.JSONDecodeError as e:
                logger.error(f"[CS] 配置 JSON 解析失败: {e}")
                self._backup_corrupt_file()
            except OSError as e:
                logger.error(f"[CS] 读取配置文件失败: {e}")
        return dict(self._DEFAULTS)

    def save(self):
        directory = os.path.dirname(self.path) or "."
        prefix = f".{os.path.basename(self.path)}."
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=directory,
                prefix=prefix,
                suffix=".tmp",
                delete=False,
            ) as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
                tmp_path = f.name
            os.replace(tmp_path, self.path)
        except OSError as e:
            logger.error(f"[CS] 保存配置文件失败: {e}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    # ── 战队关注（精确 ID 匹配）──────────────

    def follow_team(self, team_id: int, name: str, slug: str = "") -> bool:
        """关注战队，按 ID 精确匹配，避免名称歧义"""
        existing_ids = {t["id"] for t in self._data["followed_teams"] if isinstance(t, dict)}
        if team_id not in existing_ids:
            self._data["followed_teams"].append({"id": team_id, "name": name, "slug": slug})
            self.save()
            return True
        return False

    def unfollow_team(self, team_id: int) -> bool:
        """按 ID 取消关注"""
        before = len(self._data["followed_teams"])
        self._data["followed_teams"] = [
            t for t in self._data["followed_teams"]
            if not (isinstance(t, dict) and t.get("id") == team_id)
        ]
        if len(self._data["followed_teams"]) < before:
            self.save()
            return True
        return False

    def get_followed_teams(self) -> list:
        """返回关注列表，统一为 {id, name, slug} 格式（兼容旧版纯字符串格式）"""
        result = []
        for t in self._data["followed_teams"]:
            if isinstance(t, dict):
                result.append(t)
            else:
                # 兼容旧版纯字符串格式
                result.append({"id": None, "name": str(t), "slug": str(t)})
        return result

    def get_followed_team_ids(self) -> set:
        """返回已关注战队的 ID 集合（用于快速精确匹配）"""
        return {t["id"] for t in self._data["followed_teams"]
                if isinstance(t, dict) and t.get("id") is not None}

    def get_followed_team_names(self) -> list:
        """返回已关注战队名称列表（用于显示）"""
        return [
            t["name"] if isinstance(t, dict) else str(t)
            for t in self._data["followed_teams"]
        ]

    # ── 推送群组 ──────────────────────────

    def add_group(self, gid: str) -> bool:
        if gid not in self._data["push_groups"]:
            self._data["push_groups"].append(gid)
            self.save()
            return True
        return False

    def remove_group(self, gid: str) -> bool:
        if gid in self._data["push_groups"]:
            self._data["push_groups"].remove(gid)
            self.save()
            return True
        return False

    def get_groups(self) -> list:
        return self._data["push_groups"]

    # ── 提醒时间 ──────────────────────────

    def set_remind_minutes(self, m: int):
        self._data["remind_minutes"] = m
        self.save()

    def get_remind_minutes(self) -> int:
        return self._data.get("remind_minutes", 10)

    # ── 推送等级 ──────────────────────────

    def get_min_tiers(self) -> list:
        return self._data.get("min_tiers", ["s", "a"])

    # ── 赛前提醒去重 ──────────────────────

    def is_upcoming_notified(self, mid: int) -> bool:
        return mid in self._data["notified_upcoming"]

    def mark_upcoming_notified(self, mid: int):
        if mid not in self._data["notified_upcoming"]:
            self._data["notified_upcoming"].append(mid)
            self._data["notified_upcoming"] = self._data["notified_upcoming"][-500:]
            self.save()

    def clear_upcoming_notified(self, mid: int):
        if mid in self._data["notified_upcoming"]:
            self._data["notified_upcoming"].remove(mid)
            self.save()

    # ── 赛果去重 ──────────────────────────

    def is_finished_notified(self, mid: int) -> bool:
        return mid in self._data["notified_finished"]

    def mark_finished_notified(self, mid: int):
        if mid not in self._data["notified_finished"]:
            self._data["notified_finished"].append(mid)
            self._data["notified_finished"] = self._data["notified_finished"][-500:]
            self.save()

    def clear_match_notifications(self):
        self._data["notified_upcoming"] = []
        self._data["notified_finished"] = []
        self.save()

    # ── 赛程时间记录 ──────────────────────

    def get_match_schedule(self, mid: int) -> Optional[str]:
        return self._data["match_schedules"].get(str(mid))

    def set_match_schedule(self, mid: int, sched: str):
        self._data["match_schedules"][str(mid)] = sched
        self.save()

    # ── 比赛快照（检测 TBD→真实队名）──────

    def get_match_snapshot(self, mid: int) -> Optional[dict]:
        return self._data.get("match_snapshots", {}).get(str(mid))

    def set_match_snapshot(self, mid: int, sched: str, t1: str, t2: str):
        self._data.setdefault("match_snapshots", {})[str(mid)] = {
            "sched": sched, "t1": t1, "t2": t2
        }
        self.save()

    def del_match_snapshot(self, mid: int):
        self._data.get("match_snapshots", {}).pop(str(mid), None)
        self.save()

    # ── 自定义提醒时间（单场覆盖全局）──────

    def get_custom_remind(self, mid: int) -> Optional[int]:
        v = self._data.get("custom_remind_minutes", {}).get(str(mid))
        return int(v) if v is not None else None

    def set_custom_remind(self, mid: int, minutes: int):
        self._data.setdefault("custom_remind_minutes", {})[str(mid)] = minutes
        self.save()

    def del_custom_remind(self, mid: int):
        self._data.get("custom_remind_minutes", {}).pop(str(mid), None)
        self.save()

    # ── 延迟通知开关 ──────────────────────

    def get_reschedule_notify(self) -> bool:
        return self._data.get("reschedule_notify", True)

    def set_reschedule_notify(self, enabled: bool):
        self._data["reschedule_notify"] = enabled
        self.save()

    # ── 赛事开幕通知去重 ──────────────────

    def is_tournament_notified(self, tid: int) -> bool:
        return tid in self._data.get("notified_tournaments", [])

    def mark_tournament_notified(self, tid: int):
        lst = self._data.setdefault("notified_tournaments", [])
        if tid not in lst:
            lst.append(tid)
            self._data["notified_tournaments"] = lst[-300:]
            self.save()

    # ── 配置导入导出 ──────────────────────

    def export_all(self) -> dict:
        """导出所有配置（排除通知记录）"""
        d = dict(self._data)
        d.pop("pandascore_token", None)
        d.pop("web_panel_token", None)
        d.pop("notified_upcoming", None)
        d.pop("notified_finished", None)
        d.pop("notified_tournaments", None)
        d.pop("match_schedules", None)
        return d

    def import_config(self, cfg: dict):
        """从字典安全导入配置"""
        if "fetch_ahead_days" not in cfg and "fetch_ahead_hours" in cfg:
            cfg = dict(cfg)
            cfg["fetch_ahead_days"] = self._hours_to_days(cfg.get("fetch_ahead_hours"))
        safe_keys = [
            "pandascore_token",
            "followed_teams", "push_groups", "remind_minutes", "min_tiers",
            "web_panel_host", "reschedule_notify", "web_panel_port", "web_panel_enabled",
            "web_panel_token",
            "fetch_interval_min", "fetch_ahead_days",
            "custom_streams", "blacklist_teams", "blacklist_leagues",
            "notify_all_followed",
            "daily_push_enabled", "daily_push_times", "daily_push_days",
            "tournament_announce_enabled", "tournament_announce_hours",
            "image_push_enabled",
            "test_mode_enabled", "test_target_type", "test_target_id",
            "custom_remind_minutes",
        ]
        for k in safe_keys:
            if k in cfg:
                self._data[k] = cfg[k]
        self.save()
