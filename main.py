"""
main.py — 插件入口与核心调度逻辑

目录结构：
  main.py        本文件：插件注册 + 后台轮询/推送/赛事检测
  store.py       数据持久化（DataStore）
  client.py      PandaScore API 客户端
  formatters.py  消息格式化函数 + 工具函数
  web_panel.py   Web 管理面板（HTML + WebPanel 服务器）
  __init__.py    插件入口导出

唤醒词：~ 或 _（在 AstrBot 全局配置中设置）
"""

import asyncio
import io
import os
import secrets
import textwrap
from copy import deepcopy
from datetime import timedelta
from typing import Optional

from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api import logger

from .store import DataStore
from .client import PandaScoreClient
from .formatters import (
    fmt_schedule,
    now_utc, now_cst, fmt_time, parse_dt,
    team_name, match_tier, sched_str, is_push_ready_match,
    fmt_upcoming, fmt_reschedule, fmt_finished,
    fmt_tournament_announce, fmt_daily_schedule,
)
from .web_panel import WebPanel

# 固定常量
RESULT_CHECK_INTERVAL = 300   # 比赛结果轮询间隔（秒）
WEB_PANEL_PORT        = 8765  # Web 管理面板默认端口
WEB_PANEL_HOST        = "127.0.0.1"


@register(
    "astrbot_plugin_cs2_match_push",
    "CS2 比赛推送插件 + Web 管理面板",
    "每N分钟静默刷新，时间变更时提醒，精确倒计时推送赛前提醒和赛后结果，内置 Web 管理面板",
    "4.1.0",
)
class CSMatchPlugin(Star):

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self._load_config(config)
        data_dir  = StarTools.get_data_dir("astrbot_plugin_cs_match")
        self._data_dir = str(data_dir)
        self._banner_dir = os.path.join(self._data_dir, "match_banners")
        os.makedirs(self._banner_dir, exist_ok=True)
        data_file = str(data_dir / "cs_data.json")
        self.store  = DataStore(data_file)
        self._ensure_web_panel_token()
        self._sync_runtime_config_from_store()
        self.client = PandaScoreClient(self._token)
        self._scheduled: list    = []
        self._loop_task           = None
        self._daily_push_task     = None
        self._tournament_task     = None
        self._last_daily_push_key = ""
        self._match_tasks: dict   = {}   # mid -> 赛前提醒任务
        self._scheduled_mids: set = set()
        self._result_tasks: dict  = {}   # mid -> 结果轮询任务（比赛开始后独立追踪）
        self._result_meta: dict   = {}   # mid -> {attempt, deadline, match}
        self._tournament_tasks: dict = {}
        self._notified_reschedule: dict = {}
        self._team_image_cache: dict = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._fetch_schedule_lock = asyncio.Lock()
        self.panel = WebPanel(self)

        if not self._token:
            logger.warning("[CS] ⚠️ 未配置 PandaScore Token！请在 WebUI 插件配置或 Web 面板中填写 pandascore_token")
        else:
            logger.info(f"[CS] 配置加载完成: 刷新间隔={self._fetch_interval}分钟, Token={self._token[:8]}...")

    def _load_config(self, config=None):
        cfg = config or {}

        def _get(key, default):
            try:
                v = cfg.get(key)
                return v if v not in (None, "", [], {}) else default
            except AttributeError:
                return default

        def _get_int(key, default):
            try:
                return int(_get(key, default))
            except (TypeError, ValueError):
                return default

        self._token          = str(_get("pandascore_token", "") or "").strip()
        self._fetch_interval = max(1, _get_int("fetch_interval_min", 10))

        fetch_ahead_days = _get("fetch_ahead_days", None)
        if fetch_ahead_days in (None, "", [], {}):
            legacy_hours = _get("fetch_ahead_hours", None)
            if legacy_hours in (None, "", [], {}):
                self._fetch_ahead = 2
            else:
                try:
                    self._fetch_ahead = max(1, (int(legacy_hours) + 23) // 24)
                except (TypeError, ValueError):
                    self._fetch_ahead = 2
        else:
            try:
                self._fetch_ahead = max(1, int(fetch_ahead_days))
            except (TypeError, ValueError):
                self._fetch_ahead = 2

        self._web_host = str(_get("web_panel_host", WEB_PANEL_HOST) or WEB_PANEL_HOST).strip() or WEB_PANEL_HOST
        self._web_port = max(1, _get_int("web_panel_port", WEB_PANEL_PORT))

    def _sync_runtime_config_from_store(self):
        store_token = str(self.store.get("pandascore_token") or "").strip()
        if store_token:
            self._token = store_token

        try:
            self._fetch_interval = max(1, int(self.store.get("fetch_interval_min") or self._fetch_interval))
        except (TypeError, ValueError) as e:
            logger.warning(f"[CS] fetch_interval_min 配置无效，沿用当前值: {e}")

        try:
            self._fetch_ahead = max(1, int(self.store.get("fetch_ahead_days") or self._fetch_ahead))
        except (TypeError, ValueError) as e:
            logger.warning(f"[CS] fetch_ahead_days 配置无效，沿用当前值: {e}")

        self._web_host = str(self.store.get("web_panel_host") or self._web_host or WEB_PANEL_HOST).strip() or WEB_PANEL_HOST

        try:
            self._web_port = max(1, int(self.store.get("web_panel_port") or self._web_port))
        except (TypeError, ValueError) as e:
            logger.warning(f"[CS] web_panel_port 配置无效，沿用当前值: {e}")

    def _ensure_web_panel_token(self):
        if not str(self.store.get("web_panel_token") or "").strip():
            self.store.set("web_panel_token", secrets.token_urlsafe(24))

    def _create_background_task(self, coro, label: str = "background") -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(lambda t: self._log_background_task_result(t, label))
        return task

    def _log_background_task_result(self, task: asyncio.Task, label: str):
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc:
            logger.error(f"[CS] 后台任务异常({label}): {type(exc).__name__}: {exc}")

    async def reload_runtime_config(self):
        self._sync_runtime_config_from_store()
        old_client = self.client
        self.client = PandaScoreClient(self._token)
        await old_client.close()

    def _running_in_container(self) -> bool:
        return os.path.exists("/.dockerenv")

    def _web_panel_bind_host(self) -> str:
        host = str(self.store.get("web_panel_host") or self._web_host or WEB_PANEL_HOST).strip() or WEB_PANEL_HOST
        if self._running_in_container() and host in ("127.0.0.1", "::1", "localhost"):
            return "0.0.0.0"
        return host

    def _web_panel_display_host(self) -> str:
        host = self._web_panel_bind_host()
        return "localhost" if host in ("127.0.0.1", "::1", "localhost") else host

    def _web_panel_url(self, include_token: bool = False) -> str:
        web_port = self.store.get("web_panel_port") or self._web_port
        url = f"http://{self._web_panel_display_host()}:{web_port}"
        if include_token:
            token = str(self.store.get("web_panel_token") or "").strip()
            if token:
                return f"{url}/?token={token}"
        return url

    async def initialize(self):
        # 启动 Web 面板
        web_host = self._web_panel_bind_host()
        web_port = self.store.get("web_panel_port") or self._web_port
        if self.store.get("web_panel_enabled", True):
            try:
                await self.panel.start(web_host, web_port)
            except Exception as e:
                logger.error(f"[CS] Web 面板启动失败: {e}")

        self._loop_task       = asyncio.create_task(self._poll_loop())
        self._daily_push_task = asyncio.create_task(self._daily_push_loop())
        self._tournament_task = asyncio.create_task(self._tournament_announce_loop())
        logger.info(f"[CS] 插件已启动，每 {self._fetch_interval} 分钟自动刷新日程")
        self.panel.push_log("OK", f"插件启动，刷新间隔 {self._fetch_interval} 分钟")

    async def destroy(self):
        tasks_to_cancel = []
        for task in (self._loop_task, self._daily_push_task, self._tournament_task):
            if task:
                task.cancel()
                tasks_to_cancel.append(task)
        for t in self._match_tasks.values():
            t.cancel()
            tasks_to_cancel.append(t)
        for t in self._result_tasks.values():
            t.cancel()
            tasks_to_cancel.append(t)
        for t in self._tournament_tasks.values():
            t.cancel()
            tasks_to_cancel.append(t)
        for t in list(self._background_tasks):
            t.cancel()
            tasks_to_cancel.append(t)
        self._match_tasks.clear()
        self._result_tasks.clear()
        self._result_meta.clear()
        self._tournament_tasks.clear()
        self._scheduled_mids.clear()
        self._background_tasks.clear()
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        await self.panel.stop()
        await self.client.close()
        logger.info("[CS] 插件已停止")

    async def terminate(self):
        await self.destroy()

    # ── 主轮询循环 ────────────────────────

    async def _poll_loop(self):
        await self._fetch_and_schedule()
        while True:
            try:
                # 每次循环都从 store 读取最新间隔（支持运行时修改）
                interval = self.store.get("fetch_interval_min") or self._fetch_interval
                await asyncio.sleep(interval * 60)
            except asyncio.CancelledError:
                break
            await self._fetch_and_schedule()

    # ── 每日定时推送循环 ──────────────────

    async def _daily_push_loop(self):
        """每分钟检查一次是否到达推送时间点"""
        while True:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

            if not self.store.get("daily_push_enabled", False):
                continue

            push_times  = self.store.get("daily_push_times") or ["08:00"]
            now_cst_dt  = now_cst()
            hhmm        = now_cst_dt.strftime("%H:%M")

            if hhmm in push_times:
                # 防重：同一分钟只推一次
                key = f"{now_cst_dt.strftime('%Y-%m-%d')}_{hhmm}"
                if self._last_daily_push_key == key:
                    continue
                self._last_daily_push_key = key
                days = int(self.store.get("daily_push_days") or 1)
                logger.info(f"[CS] 触发每日定时推送 {hhmm}，推送 {days} 天赛程")
                self.panel.push_log("OK", f"每日定时推送触发 {hhmm}")
                await self._do_instant_push(days)

    # ── 赛事开幕推送循环 ──────────────────

    async def _tournament_announce_loop(self):
        """每次日程刷新后检查即将开赛的赛事，提前 announce_hours 推送开幕通知"""
        # 等待首次日程刷新完成后再开始
        await asyncio.sleep(30)
        while True:
            try:
                await self._check_tournament_announces()
                interval = (self.store.get("fetch_interval_min") or self._fetch_interval) * 60
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CS] 赛事开幕检测异常: {e}")
                await asyncio.sleep(300)

    async def _check_tournament_announces(self):
        """检查即将开始的赛事，符合条件则安排开幕通知倒计时任务"""
        if not self.store.get("tournament_announce_enabled", True):
            return
        if not self._token:
            return

        tournaments = await self.client.get_upcoming_tournaments(per_page=50)
        if not tournaments:
            return

        followed_ids    = self.store.get_followed_team_ids()
        followed_names  = [t.lower() for t in self.store.get_followed_team_names()]
        push_tiers      = self.store.get_min_tiers()
        notify_all_fol  = self.store.get("notify_all_followed", True)
        announce_hours  = int(self.store.get("tournament_announce_hours") or 2)
        blacklist_lgues = self.store.get("blacklist_leagues") or []
        blacklist_teams = self.store.get("blacklist_teams") or []
        now             = now_utc()
        cutoff          = now + timedelta(days=7)

        for tour in tournaments:
            tid      = tour.get("id")
            begin_at = tour.get("begin_at")
            if not tid or not begin_at:
                continue
            try:
                begin_dt = parse_dt(begin_at)
            except Exception:
                continue
            if not (now < begin_dt <= cutoff):
                continue

            league_name = ((tour.get("league") or {}).get("name") or "").lower()
            if any(b in league_name for b in blacklist_lgues):
                continue

            # 战队列表（含 id 和 name）
            tour_teams = [t for t in (tour.get("teams") or []) if isinstance(t, dict)]
            tour_ids   = {t.get("id") for t in tour_teams if t.get("id")}
            tour_names = [t.get("name", "").lower() for t in tour_teams]

            if any(b in tn for b in blacklist_teams for tn in tour_names if tn):
                continue

            tier = match_tier({"tournament": tour})
            # ID 精确匹配，兼容旧版名称匹配
            has_followed = bool(tour_ids & followed_ids) or (
                bool(followed_names) and any(fn == tn for fn in followed_names for tn in tour_names)
            )

            # 判断是否推送：级别符合 OR 关注战队参赛
            should_push = (tier in push_tiers) or (has_followed and notify_all_fol)
            if not should_push:
                continue

            if self.store.is_tournament_notified(tid):
                continue
            if tid in self._tournament_tasks and not self._tournament_tasks[tid].done():
                continue

            t = asyncio.create_task(
                self._schedule_tournament_announce(tour, announce_hours, has_followed)
            )
            self._tournament_tasks[tid] = t
            logger.info(f"[CS] 已安排赛事 {tid}({tour.get('name')}) 的开幕通知任务")

    async def _schedule_tournament_announce(self, tour: dict, announce_hours: int,
                                            is_followed: bool):
        """等待到开赛前 announce_hours 小时，然后推送开幕通知"""
        tid      = tour.get("id")
        try:
            begin_dt = parse_dt(tour.get("begin_at"))
            announce_dt = begin_dt - timedelta(hours=announce_hours)
            wait_secs   = (announce_dt - now_utc()).total_seconds()

            if wait_secs > 0:
                logger.info(f"[CS] 赛事 {tid} 将在 {wait_secs/3600:.1f} 小时后推送开幕通知")
                try:
                    await asyncio.sleep(wait_secs)
                except asyncio.CancelledError:
                    logger.info(f"[CS] 赛事 {tid} 开幕通知任务已取消")
                    raise

            # 推送前再次检查是否已通知
            if self.store.is_tournament_notified(tid):
                return

            self.store.mark_tournament_notified(tid)
            followed = self.store.get_followed_team_names()
            msg = fmt_tournament_announce(tour, followed, announce_hours, is_followed)
            await self._push(msg)
            self.panel.push_log("OK", f"已推送赛事开幕通知：{tour.get('name')} (id={tid})")
            logger.info(f"[CS] 已推送赛事开幕通知：{tour.get('name')}")
        finally:
            if tid is not None:
                self._tournament_tasks.pop(tid, None)

    async def _do_instant_push(self, days: int = 1):
        """立即将赛程推送到所有群"""
        followed = self.store.get_followed_team_names()
        if not self._has_daily_matches_to_push(days):
            logger.info(f"[CS] 跳过赛程日报推送：未来 {days} 天内没有可推送的已安排比赛")
            self.panel.push_log("INFO", f"跳过赛程日报推送：未来 {days} 天内无可推送比赛")
            return False
        matches = [self._match_with_snapshot_names(m) for m in self._scheduled]
        text = fmt_daily_schedule(matches, followed, days)
        await self._push(text)
        self.panel.push_log("OK", f"立即推送赛程完成，覆盖 {days} 天")
        return True

    def _has_daily_matches_to_push(self, days: int) -> bool:
        now_cst_dt = now_cst()
        for raw_match in self._scheduled:
            match = self._match_with_snapshot_names(raw_match)
            if not is_push_ready_match(match):
                continue
            sched = sched_str(match)
            if not sched:
                continue
            try:
                dt_cst = parse_dt(sched).astimezone(now_cst_dt.tzinfo)
            except Exception:
                continue
            delta = (dt_cst.date() - now_cst_dt.date()).days
            if 0 <= delta < days:
                return True
        return False

    async def _fetch_and_schedule(self):
        if self._fetch_schedule_lock.locked():
            logger.info("[CS] 日程刷新已在进行，跳过本次重复触发")
            self.panel.push_log("INFO", "日程刷新已在进行，已跳过重复触发")
            return
        async with self._fetch_schedule_lock:
            await self._fetch_and_schedule_locked()

    async def _fetch_and_schedule_locked(self):
        if not self._token:
            return

        matches = await self.client.get_upcoming_matches(per_page=100)
        if not matches:
            logger.warning("[CS] 未获取到比赛数据")
            self.panel.push_log("WARN", "未获取到比赛数据")
            return

        followed_ids    = self.store.get_followed_team_ids()
        followed_names  = [t.lower() for t in self.store.get_followed_team_names()]  # 旧版兼容
        push_tiers      = self.store.get_min_tiers()
        blacklist_teams = self.store.get("blacklist_teams") or []
        blacklist_lgues = self.store.get("blacklist_leagues") or []
        notify_all_fol  = self.store.get("notify_all_followed", True)
        now             = now_utc()
        fetch_ahead     = self.store.get("fetch_ahead_days") or self._fetch_ahead
        cutoff          = now + timedelta(days=fetch_ahead)

        def _is_followed_match(m: dict) -> bool:
            """精确判断比赛是否包含关注战队（ID 优先，兼容旧版名称匹配）"""
            for opp in (m.get("opponents") or []):
                opp_id   = (opp.get("opponent") or {}).get("id")
                opp_name = ((opp.get("opponent") or {}).get("name") or "").lower()
                if opp_id and opp_id in followed_ids:
                    return True
                # 旧版兼容：纯名称匹配（仅当无 ID 记录时）
                if followed_names and any(fn == opp_name for fn in followed_names):
                    return True
            return False

        to_schedule = []
        for m in matches:
            s = sched_str(m)
            if not s:
                continue
            try:
                sched_dt = parse_dt(s)
            except Exception:
                continue
            if not (now < sched_dt <= cutoff):
                continue

            t1l    = team_name(m, 0).lower()
            t2l    = team_name(m, 1).lower()
            league = ((m.get("league") or {}).get("name") or "").lower()
            if any(b in t1l or b in t2l for b in blacklist_teams):
                continue
            if any(b in league for b in blacklist_lgues):
                continue

            tier         = match_tier(m)
            has_followed = _is_followed_match(m)

            if tier in push_tiers or (has_followed and notify_all_fol):
                to_schedule.append(m)

        self._scheduled = to_schedule
        self.panel.push_log("INFO", f"日程刷新完成，共 {len(to_schedule)} 场符合条件的比赛")

        # ── 清理不再在列表中的旧任务 ──────────────────────────────────────
        current_mids = {m["id"] for m in to_schedule}
        for mid in list(self._scheduled_mids):
            if mid not in current_mids:
                task = self._match_tasks.get(mid)
                if task and not task.done():
                    task.cancel()
                    logger.info(f"[CS] 取消不再符合条件的比赛 {mid} 提醒任务")
                self._scheduled_mids.discard(mid)
                self._match_tasks.pop(mid, None)
                self.store.del_match_snapshot(mid)

        # ── 为每场比赛安排/更新提醒任务 ──────────────────────────────────
        global_remind = self.store.get_remind_minutes()

        for match in to_schedule:
            mid       = match["id"]
            new_sched = sched_str(match)
            new_t1    = team_name(match, 0)
            new_t2    = team_name(match, 1)
            old_snap  = self.store.get_match_snapshot(mid)

            # 检测变更：时间变更 OR 队伍名从 TBD 变为真实队名
            needs_rebuild = False
            change_reason = None

            if old_snap:
                sched_changed = old_snap.get("sched") != new_sched
                t1_changed    = old_snap.get("t1") == "TBD" and new_t1 != "TBD"
                t2_changed    = old_snap.get("t2") == "TBD" and new_t2 != "TBD"

                if sched_changed:
                    old_fmt = fmt_time(old_snap.get("sched"))
                    new_fmt = fmt_time(new_sched)
                    logger.info(f"[CS] 比赛 {mid} 时间变更：{old_fmt} → {new_fmt}")
                    self.panel.push_log("WARN", f"比赛 {mid} 时间变更 {old_fmt} → {new_fmt}")
                    last_notified = self._notified_reschedule.get(mid)
                    if last_notified != new_sched and self.store.get_reschedule_notify():
                        self._notified_reschedule[mid] = new_sched
                        await self._push(fmt_reschedule(match, old_fmt, new_fmt))
                    needs_rebuild = True
                    change_reason = f"时间变更 {old_fmt}→{new_fmt}"

                if t1_changed or t2_changed:
                    changed_teams = []
                    if t1_changed:
                        changed_teams.append(f"队伍1: TBD→{new_t1}")
                    if t2_changed:
                        changed_teams.append(f"队伍2: TBD→{new_t2}")
                    reason = "、".join(changed_teams)
                    logger.info(f"[CS] 比赛 {mid} 队伍确认：{reason}")
                    self.panel.push_log("INFO", f"比赛 {mid} 队伍确认：{reason}")
                    needs_rebuild = True
                    change_reason = (change_reason + " | " if change_reason else "") + reason

            # 保存最新快照
            self.store.set_match_snapshot(mid, new_sched, new_t1, new_t2)
            self.store.set_match_schedule(mid, new_sched)

            if self.store.is_finished_notified(mid):
                continue

            # 需要重建任务（时间或队伍变更）
            if needs_rebuild and mid in self._scheduled_mids:
                task = self._match_tasks.get(mid)
                if task and not task.done():
                    task.cancel()
                self.store.clear_upcoming_notified(mid)
                self._scheduled_mids.discard(mid)
                self._match_tasks.pop(mid, None)
                logger.info(f"[CS] 重建比赛 {mid} 提醒任务（{change_reason}）")
                self.panel.push_log("INFO", f"重建比赛 {mid} 任务：{change_reason}")

            if mid in self._scheduled_mids:
                continue

            # 使用自定义提醒时间（若有），否则用全局设置
            remind_min = self.store.get_custom_remind(mid)
            if remind_min is None:
                remind_min = global_remind

            t = asyncio.create_task(self._schedule_match(match, remind_min))
            self._match_tasks[mid] = t
            self._scheduled_mids.add(mid)
            logger.info(f"[CS] 已安排比赛 {mid}（{new_t1} vs {new_t2}）提醒任务，提前 {remind_min} 分钟")

    async def _schedule_match(self, match: dict, remind_min: int):
        mid = match["id"]
        cancelled = False
        try:
            await self._run_remind(match, remind_min)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            self._match_tasks.pop(mid, None)
            self._scheduled_mids.discard(mid)
            # 若比赛未结束，交给独立的结果轮询任务
            if (
                not cancelled
                and not self.store.is_finished_notified(mid)
                and mid not in self._result_tasks
            ):
                t = asyncio.create_task(self._run_result_poll(match))
                self._result_tasks[mid] = t

    async def _run_remind(self, match: dict, remind_min: int):
        """阶段1：等待赛前提醒时间点，推送提醒，然后等到开赛时间返回"""
        mid      = match["id"]
        sched_dt = parse_dt(sched_str(match))

        # ── 赛前提醒 ──────────────────────────────────────────────────────────
        remind_dt   = sched_dt - timedelta(minutes=remind_min)
        wait_remind = (remind_dt - now_utc()).total_seconds()

        if wait_remind > 0 and not self.store.is_upcoming_notified(mid):
            logger.info(f"[CS] 比赛 {mid} 将在 {wait_remind/60:.1f} 分钟后推送提醒")
            try:
                await asyncio.sleep(wait_remind)
            except asyncio.CancelledError:
                logger.info(f"[CS] 比赛 {mid} 提醒任务取消（时间变更）")
                raise
            if not self.store.is_upcoming_notified(mid):
                self.store.mark_upcoming_notified(mid)
                custom_streams = self.store.get("custom_streams") or []
                followed       = self.store.get_followed_team_names()
                await self._push_upcoming(match, remind_min, custom_streams, followed)
                self.panel.push_log("OK", f"已推送赛前提醒：比赛 {mid}")
                logger.info(f"[CS] 已推送赛前提醒 比赛 {mid}")

        if self.store.is_finished_notified(mid):
            return

        # ── 等待到开赛时间 ────────────────────────────────────────────────────
        wait_start = (sched_dt - now_utc()).total_seconds()
        if wait_start > 0:
            try:
                await asyncio.sleep(wait_start)
            except asyncio.CancelledError:
                logger.info(f"[CS] 比赛 {mid} 开赛等待任务取消")
                raise

    async def _run_result_poll(self, match: dict):
        """阶段2：开赛后独立轮询结果，记录进度供 Web 面板展示"""
        mid       = match["id"]
        num_games = match.get("number_of_games", 1) or 1
        logger.info(f"[CS] 比赛 {mid} BO{num_games} 开始轮询结果")

        max_query_seconds = (5 * 3600) + 3600
        deadline  = now_utc() + timedelta(seconds=max_query_seconds)
        attempt   = 0

        self._result_meta[mid] = {
            "match":    match,
            "attempt":  0,
            "deadline": deadline.isoformat(),
            "started":  now_utc().isoformat(),
        }

        try:
            while True:
                if now_utc() >= deadline:
                    logger.warning(f"[CS] 比赛 {mid} 超过截止时间，放弃查询结果")
                    self.panel.push_log("WARN", f"比赛 {mid} 超时放弃（已查询 {attempt} 次）")
                    return

                if self.store.is_finished_notified(mid):
                    return

                result = await self.client.get_match_result(mid)
                attempt += 1
                self._result_meta[mid]["attempt"] = attempt

                if result and result.get("status") == "finished":
                    if self.store.is_finished_notified(mid):
                        return
                    self.store.mark_finished_notified(mid)
                    followed = self.store.get_followed_team_names()
                    await self._push_finished(result, followed)
                    self.panel.push_log("OK", f"已推送赛后结果：比赛 {mid}（查询 {attempt} 次）")
                    logger.info(f"[CS] 已推送赛后结果 比赛 {mid}（查询 {attempt} 次）")
                    return

                interval  = RESULT_CHECK_INTERVAL if attempt <= 6 else RESULT_CHECK_INTERVAL * 3
                remaining = (deadline - now_utc()).total_seconds()
                if remaining <= 0:
                    continue
                try:
                    await asyncio.sleep(min(interval, remaining))
                except asyncio.CancelledError:
                    raise
        finally:
            self._result_tasks.pop(mid, None)
            self._result_meta.pop(mid, None)

    async def _push_upcoming(self, match: dict, remind_min: int,
                             custom_streams: list, followed: list,
                             text_prefix: str = "",
                             test_target: bool = False):
        match = self._match_with_snapshot_names(match)
        push_text = self._push_test if test_target else self._push
        push_components = self._push_test_components if test_target else self._push_components
        text = fmt_upcoming(match, remind_min, custom_streams, followed)
        if text_prefix:
            text = f"{text_prefix}\n{text}"
        if not self.store.get("image_push_enabled", True):
            await push_text(text)
            return
        banner_path = await self._build_match_logo_banner(match)
        if not banner_path:
            await push_text(text)
            return

        _, Plain, Image = self._message_component_types()
        before_text, after_text = self._split_upcoming_text(text)
        banner = self._make_image_file_component(Image, banner_path)
        if banner is None:
            await push_text(text)
            return

        components = []
        if before_text:
            components.append(self._make_plain_component(Plain, before_text))
        components.append(banner)
        if after_text:
            components.append(self._make_plain_component(Plain, after_text))
        await push_components(components)

    async def _push_finished(self, match: dict, followed: list,
                             text_prefix: str = "",
                             test_target: bool = False):
        match = self._match_with_snapshot_names(match)
        push_text = self._push_test if test_target else self._push
        push_components = self._push_test_components if test_target else self._push_components
        text = fmt_finished(match, followed)
        if text_prefix:
            text = f"{text_prefix}\n{text}"
        if not self.store.get("image_push_enabled", True):
            await push_text(text)
            return

        score_text = self._get_match_score_text(match)
        banner_path = await self._build_match_logo_banner(match, center_text=score_text, kind="result")
        if not banner_path:
            await push_text(text)
            return

        _, Plain, Image = self._message_component_types()
        before_text, after_text = self._split_finished_text(text)
        banner = self._make_image_file_component(Image, banner_path)
        if banner is None:
            await push_text(text)
            return

        components = []
        if before_text:
            components.append(self._make_plain_component(Plain, before_text))
        components.append(banner)
        if after_text:
            components.append(self._make_plain_component(Plain, after_text))
        await push_components(components)

    def _test_prefix(self, text: str) -> str:
        return f"[测试]\n{text}"

    def _extract_message_id(self, result):
        if result is None:
            return None
        if isinstance(result, dict):
            for key in ("message_id", "messageId", "msg_id", "id"):
                value = result.get(key)
                if value is not None:
                    return str(value)
            data = result.get("data")
            if isinstance(data, dict):
                for key in ("message_id", "messageId", "msg_id", "id"):
                    value = data.get(key)
                    if value is not None:
                        return str(value)
        for attr in ("message_id", "messageId", "msg_id", "id"):
            value = getattr(result, attr, None)
            if value is not None:
                return str(value)
        return None

    def _match_with_snapshot_names(self, match: dict) -> dict:
        if not isinstance(match, dict):
            return match
        enriched = deepcopy(match)
        mid = enriched.get("id")
        if mid is None:
            return enriched

        snap = self.store.get_match_snapshot(mid) or {}
        for idx, key in enumerate(("t1", "t2")):
            current_name = team_name(enriched, idx)
            snap_name = str(snap.get(key) or "").strip()
            resolved_name = current_name
            if current_name == "TBD" and snap_name and snap_name != "TBD":
                resolved_name = snap_name
            if resolved_name and resolved_name != "TBD":
                enriched[f"_{key}"] = resolved_name
                self._ensure_match_opponent_name(enriched, idx, resolved_name)
        return enriched

    def _ensure_match_opponent_name(self, match: dict, idx: int, name: str):
        opponents = match.setdefault("opponents", [])
        while len(opponents) <= idx:
            opponents.append({})
        slot = opponents[idx] if isinstance(opponents[idx], dict) else {}
        opponent = slot.get("opponent")
        if not isinstance(opponent, dict):
            opponent = {}
        current_name = str(opponent.get("name") or "").strip()
        if not current_name or current_name == "TBD":
            opponent["name"] = name
        slot["opponent"] = opponent
        opponents[idx] = slot

    async def _get_match_logo_urls(self, match: dict) -> list[str]:
        urls = []
        for idx in range(2):
            url = await self._get_team_logo_url(match, idx)
            urls.append(url)
        return urls

    async def _download_match_logo_images(self, logo_urls: list[str]) -> list[bytes | None]:
        logo_data: list[bytes | None] = []
        for url in logo_urls[:2]:
            logo_data.append(await self.client.fetch_bytes(url) if url else None)
        while len(logo_data) < 2:
            logo_data.append(None)
        return logo_data

    def _get_match_score_text(self, match: dict) -> str:
        score_map = {
            r["team_id"]: r["score"]
            for r in match.get("results", []) if "team_id" in r
        }

        def _team_id(idx: int):
            try:
                return match["opponents"][idx]["opponent"]["id"]
            except Exception:
                return None

        score1 = score_map.get(_team_id(0), "-")
        score2 = score_map.get(_team_id(1), "-")
        return f"{score1} : {score2}"

    def _split_upcoming_text(self, text: str) -> tuple[str, str]:
        lines = text.splitlines()
        before_lines = []
        after_lines = []
        stream_section = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("⚔"):
                continue
            if stripped.startswith("📺"):
                stream_section = True
            if stream_section:
                after_lines.append(line)
            else:
                before_lines.append(line)
        before = "\n".join(before_lines).strip()
        after = "\n".join(after_lines).strip()
        return before, after

    def _split_finished_text(self, text: str) -> tuple[str, str]:
        lines = text.splitlines()
        before_lines = []
        after_lines = []
        matchup_removed = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("⚔"):
                matchup_removed = True
                continue
            if matchup_removed:
                after_lines.append(line)
            else:
                before_lines.append(line)
        before = "\n".join(before_lines).strip()
        after = "\n".join(after_lines).strip()
        return before, after

    async def _build_match_logo_banner(
        self,
        match: dict,
        center_text: str = "VS",
        kind: str = "match",
    ) -> str:
        match = self._match_with_snapshot_names(match)
        logo_urls = await self._get_match_logo_urls(match)
        logo_images = await self._download_match_logo_images(logo_urls[:2])
        team_names = [team_name(match, 0), team_name(match, 1)]
        output_path = os.path.join(self._banner_dir, f"{kind}_{match.get('id', 'unknown')}.png")
        return await asyncio.to_thread(
            self._render_match_logo_banner,
            logo_images,
            team_names,
            center_text,
            output_path,
            kind,
        )

    def _render_match_logo_banner(
        self,
        logo_images: list[bytes | None],
        team_names: list[str],
        center_text: str,
        output_path: str,
        kind: str = "match",
    ) -> str:
        try:
            from PIL import Image as PILImage, ImageDraw, ImageFont
        except ImportError:
            return ""

        try:
            # 赛前图尽量与赛后图保持同样的视觉尺寸
            banner_width = 320
            banner_height = 88
            logo_size = 50
            logo_box = 54
            left_center_x = 82
            right_center_x = 238
            logo_y = 3

            logos = []
            for data in logo_images[:2]:
                canvas = None
                if data:
                    try:
                        logo = PILImage.open(io.BytesIO(data)).convert("RGBA")
                        logo.thumbnail((logo_size, logo_size), PILImage.LANCZOS)
                        canvas = PILImage.new("RGBA", (logo_box, logo_box), (0, 0, 0, 0))
                        offset = ((logo_box - logo.width) // 2, (logo_box - logo.height) // 2)
                        canvas.alpha_composite(logo, offset)
                    except (OSError, ValueError):
                        canvas = None
                logos.append(canvas)
            while len(logos) < 2:
                logos.append(None)

            banner = PILImage.new("RGBA", (banner_width, banner_height), (255, 255, 255, 255))
            draw = ImageDraw.Draw(banner)
            if logos[0] is not None:
                banner.alpha_composite(logos[0], (left_center_x - logos[0].width // 2, logo_y))
            else:
                self._draw_banner_logo_placeholder(draw, left_center_x, logo_y, team_names[0])
            if logos[1] is not None:
                banner.alpha_composite(logos[1], (right_center_x - logos[1].width // 2, logo_y))
            else:
                self._draw_banner_logo_placeholder(draw, right_center_x, logo_y, team_names[1])

            center_max_w = 84
            name_max_w = 112
            center_font = self._fit_banner_font(draw, center_text, center_max_w, 19, 12)
            name_font_left = self._fit_banner_font(draw, team_names[0], name_max_w, 14, 9)
            name_font_right = self._fit_banner_font(draw, team_names[1], name_max_w, 14, 9)
            left_name = self._shorten_banner_text(draw, team_names[0], name_font_left, name_max_w)
            right_name = self._shorten_banner_text(draw, team_names[1], name_font_right, name_max_w)
            center_text = self._shorten_banner_text(draw, center_text, center_font, center_max_w)
            self._draw_centered_text(
                draw, center_text, banner.width // 2, 22, center_font,
                fill=(0, 0, 0, 255),
            )

            self._draw_centered_text(
                draw, left_name, left_center_x, 60, name_font_left,
                fill=(34, 34, 34, 255),
            )
            self._draw_centered_text(
                draw, right_name, right_center_x, 60, name_font_right,
                fill=(34, 34, 34, 255),
            )

            banner.save(output_path, format="PNG")
            return output_path
        except Exception:
            return ""

    def _draw_banner_logo_placeholder(self, draw, center_x: int, top_y: int, team_name_text: str):
        left = center_x - 24
        right = center_x + 24
        bottom = top_y + 48
        draw.rounded_rectangle(
            (left, top_y, right, bottom),
            radius=10,
            fill=(245, 245, 245, 255),
            outline=(205, 205, 205, 255),
            width=2,
        )
        placeholder = self._banner_placeholder_text(team_name_text)
        font = self._fit_banner_font(draw, placeholder, 30, 11, 8)
        self._draw_centered_text(draw, placeholder, center_x, top_y + 13, font, fill=(70, 70, 70, 255))

    def _banner_placeholder_text(self, team_name_text: str) -> str:
        tokens = [token for token in str(team_name_text or "").replace("-", " ").split() if token]
        if tokens:
            return "".join(token[0].upper() for token in tokens[:3])
        compact = "".join(ch for ch in str(team_name_text or "") if ch.isalnum())
        if compact:
            return compact[:3].upper()
        return "?"

    def _pick_banner_font(self, size: int):
        from PIL import ImageFont

        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/msyh.ttc",
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _fit_banner_font(self, draw, text: str, max_width: int, start_size: int, min_size: int):
        for size in range(start_size, min_size - 1, -1):
            font = self._pick_banner_font(size)
            bbox = draw.textbbox((0, 0), text, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                return font
        return self._pick_banner_font(min_size)

    def _shorten_banner_text(self, draw, text: str, font, max_width: int) -> str:
        if not text:
            return ""
        candidate = text
        while candidate:
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                return candidate
            if len(candidate) <= 4:
                return candidate[: max(1, len(candidate) - 1)]
            candidate = candidate[:-2].rstrip() + "..."
        return text

    def _draw_centered_text(
        self, draw, text: str, center_x: int, top_y: int, font, fill,
        stroke_fill=None, stroke_width: int = 0,
    ):
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        x = center_x - (bbox[2] - bbox[0]) // 2
        draw.text(
            (x, top_y),
            text,
            font=font,
            fill=fill,
            stroke_fill=stroke_fill,
            stroke_width=stroke_width,
        )

    async def _get_team_logo_url(self, match: dict, idx: int) -> str:
        try:
            opponent = ((match.get("opponents") or [])[idx] or {}).get("opponent") or {}
        except Exception:
            opponent = {}

        image_url = (opponent.get("image_url") or "").strip()
        team_id = opponent.get("id")
        if not image_url and team_id:
            cached = self._team_image_cache.get(team_id)
            if cached is None:
                team = await self.client.get_team(team_id)
                cached = ((team or {}).get("image_url") or "").strip()
                self._team_image_cache[team_id] = cached
            image_url = cached
        return self._thumb_logo_url(image_url)

    def _thumb_logo_url(self, image_url: str) -> str:
        if not image_url:
            return ""
        head, sep, tail = image_url.rpartition("/")
        if not sep or not tail:
            return image_url
        if tail.startswith("thumb_"):
            return image_url
        if tail.startswith("normal_"):
            tail = tail[len("normal_"):]
        return f"{head}/thumb_{tail}"

    def _message_component_types(self):
        from astrbot.api.event import MessageChain
        import astrbot.api.message_components as Comp
        return MessageChain, Comp.Plain, Comp.Image

    def _make_plain_component(self, Plain, text: str):
        try:
            return Plain(text=text)
        except TypeError:
            return Plain(text)

    def _make_image_component(self, Image, url: str):
        if not url:
            return None
        try:
            return Image.fromURL(url=url)
        except TypeError:
            return Image.fromURL(url)
        except Exception:
            return None

    def _make_image_file_component(self, Image, path: str):
        if not path or not os.path.exists(path):
            return None
        builders = [
            ("fromFileSystem", ("path", path)),
            ("fromFile", ("path", path)),
            ("fromPath", ("path", path)),
            ("fromLocalFile", ("path", path)),
        ]
        for method_name, (kwarg, value) in builders:
            method = getattr(Image, method_name, None)
            if method is None:
                continue
            try:
                return method(**{kwarg: value})
            except TypeError:
                try:
                    return method(value)
                except Exception:
                    continue
            except Exception:
                continue
        return None

    def _build_message_chain(self, components: list):
        MessageChain, _, _ = self._message_component_types()
        try:
            return MessageChain(chain=components)
        except TypeError:
            chain = MessageChain()
            if hasattr(chain, "chain"):
                chain.chain = components
                return chain
            raise

    async def _push(self, text: str):
        _, Plain, _ = self._message_component_types()
        return await self._push_components([self._make_plain_component(Plain, text)])

    def _get_test_target(self) -> Optional[tuple[str, str]]:
        target_type = str(self.store.get("test_target_type", "private") or "private").strip().lower()
        target_id = str(self.store.get("test_target_id", "") or "").strip()
        if target_type not in {"private", "group"}:
            target_type = "private"
        if not target_id.isdigit():
            return None
        return target_type, target_id

    def _format_target_label(self, target_type: str, target_id: str) -> str:
        return f"{'私聊' if target_type == 'private' else '群聊'} {target_id}"

    def _describe_test_target(self) -> str:
        target = self._get_test_target()
        if not target:
            return "（未配置）"
        return self._format_target_label(*target)

    async def _push_test(self, text: str):
        _, Plain, _ = self._message_component_types()
        return await self._push_test_components([self._make_plain_component(Plain, text)])

    async def _push_test_components(self, components: list):
        target = self._get_test_target()
        if not target:
            logger.warning("[CS] 未配置测试消息目标")
            self.panel.push_log("WARN", "未配置测试消息目标")
            return
        return await self._push_components_to_targets_with_refs([target], components)

    async def _push_components(self, components: list):
        groups = self.store.get_groups()
        if groups:
            return await self._push_components_to_targets_with_refs([("group", gid) for gid in groups], components)
        if not groups:
            logger.warning("[CS] 未配置推送群")
            return
        chain = self._build_message_chain(components)
        for gid in groups:
            try:
                await StarTools.send_message_by_id(
                    type="GroupMessage",
                    id=gid,
                    message_chain=chain,
                    platform="aiocqhttp"
                )
                logger.info(f"[CS] 已推送 -> 群 {gid}")
                self.panel.push_log("OK", f"消息已推送到群 {gid}")
            except Exception as e:
                logger.error(f"[CS] 推送失败 群={gid}: {e}")
                self.panel.push_log("ERROR", f"推送失败 群={gid}: {e}")

    # ── 指令 ──────────────────────────────

    async def _push_components_to_targets(self, targets: list[tuple[str, str]], components: list):
        if not targets:
            return
        chain = self._build_message_chain(components)
        for target_type, target_id in targets:
            message_type = "FriendMessage" if target_type == "private" else "GroupMessage"
            label = self._format_target_label(target_type, target_id)
            try:
                await StarTools.send_message_by_id(
                    type=message_type,
                    id=target_id,
                    message_chain=chain,
                    platform="aiocqhttp"
                )
                logger.info(f"[CS] 已推送 -> {label}")
                self.panel.push_log("OK", f"消息已推送到 {label}")
            except Exception as e:
                logger.error(f"[CS] 推送失败 {label}: {e}")
                self.panel.push_log("ERROR", f"推送失败 {label}: {e}")

    async def _push_components_to_targets_with_refs(self, targets: list[tuple[str, str]], components: list):
        if not targets:
            return []
        chain = self._build_message_chain(components)
        sent_refs = []
        for target_type, target_id in targets:
            message_type = "FriendMessage" if target_type == "private" else "GroupMessage"
            label = self._format_target_label(target_type, target_id)
            try:
                result = await StarTools.send_message_by_id(
                    type=message_type,
                    id=target_id,
                    message_chain=chain,
                    platform="aiocqhttp",
                )
                ref = {
                    "target_type": target_type,
                    "target_id": target_id,
                    "result": result,
                }
                message_id = self._extract_message_id(result)
                if message_id:
                    ref["message_id"] = message_id
                sent_refs.append(ref)
                logger.info(f"[CS] 已推送 -> {label}")
                self.panel.push_log("OK", f"消息已推送到 {label}")
            except Exception as e:
                logger.error(f"[CS] 推送失败 {label}: {e}")
                self.panel.push_log("ERROR", f"推送失败 {label}: {e}")
        return sent_refs

    def _is_test_mode_enabled(self) -> bool:
        return bool(self.store.get("test_mode_enabled", False))

    def _set_test_mode_enabled(self, enabled: bool):
        self.store.set("test_mode_enabled", bool(enabled))
        self.panel.push_log("INFO", f"测试模式已{'开启' if enabled else '关闭'}")

    def _build_test_match(self) -> dict:
        match = next((deepcopy(m) for m in self._scheduled if is_push_ready_match(m)), None)
        if match:
            test_time = (now_utc() + timedelta(minutes=15)).isoformat()
            match["scheduled_at"] = test_time
            if match.get("begin_at"):
                match["begin_at"] = test_time
            return match
        return {
            "id": 99000001,
            "name": "Test Match",
            "scheduled_at": (now_utc() + timedelta(minutes=15)).isoformat(),
            "number_of_games": 3,
            "league": {"name": "Test League"},
            "tournament": {"name": "Test Cup", "tier": "a"},
            "streams_list": [{"main": True, "raw_url": "https://www.twitch.tv/pgl"}],
            "opponents": [
                {"opponent": {"id": 9001, "name": "Test Alpha", "image_url": ""}},
                {"opponent": {"id": 9002, "name": "Test Bravo", "image_url": ""}},
            ],
        }

    def _build_test_finished_match(self) -> dict:
        match = self._build_test_match()
        t1 = ((match.get("opponents") or [{}])[0].get("opponent") or {})
        t2 = ((match.get("opponents") or [{}, {}])[1].get("opponent") or {})
        t1_id = t1.get("id", 9001)
        t2_id = t2.get("id", 9002)
        t1_name = t1.get("name", "Test Alpha")
        match["results"] = [
            {"team_id": t1_id, "score": 2},
            {"team_id": t2_id, "score": 1},
        ]
        match["winner"] = {"id": t1_id, "name": t1_name}
        match["games"] = [
            {"position": 1, "status": "finished", "winner": {"id": t1_id}},
            {"position": 2, "status": "finished", "winner": {"id": t2_id}},
            {"position": 3, "status": "finished", "winner": {"id": t1_id}},
        ]
        return match

    def _build_test_tournament(self) -> dict:
        match = self._build_test_match()
        teams = []
        for opp in match.get("opponents") or []:
            team = (opp or {}).get("opponent") or {}
            if team.get("name"):
                teams.append({"id": team.get("id"), "name": team.get("name")})
        begin_at = sched_str(match) or (now_utc() + timedelta(hours=2)).isoformat()
        return {
            "id": 88000001,
            "name": "CS2 Test Invitational",
            "tier": ((match.get("tournament") or {}).get("tier") or "a").upper(),
            "league": match.get("league") or {"name": "Test League"},
            "begin_at": begin_at,
            "end_at": (parse_dt(begin_at) + timedelta(days=2)).isoformat(),
            "country": "Online",
            "teams": teams,
        }

    async def _run_test_push(self, action: str, days: int = 1, event: AstrMessageEvent | None = None):
        followed = self.store.get_followed_team_names()
        if action == "赛前":
            match = self._build_test_match()
            custom_streams = self.store.get("custom_streams") or []
            await self._push_upcoming(
                match,
                self.store.get_remind_minutes(),
                custom_streams,
                followed,
                text_prefix="[测试]",
                test_target=True,
            )
            return "已发送测试赛前提醒"
        if action == "赛果":
            await self._push_finished(
                self._build_test_finished_match(),
                followed,
                text_prefix="[测试]",
                test_target=True,
            )
            return "已发送测试赛果推送"
        if action == "变更":
            match = self._build_test_match()
            old_time = fmt_time((now_utc() + timedelta(hours=1)).isoformat())
            new_time = fmt_time((now_utc() + timedelta(hours=3)).isoformat())
            await self._push_test(self._test_prefix(fmt_reschedule(match, old_time, new_time)))
            return "已发送测试赛程变更通知"
        if action == "开幕":
            await self._push_test(self._test_prefix(fmt_tournament_announce(
                self._build_test_tournament(),
                followed,
                int(self.store.get("tournament_announce_hours") or 2),
                False,
            )))
            return "已发送测试赛事开幕通知"
        if action == "日报":
            await self._push_test(self._test_prefix(fmt_daily_schedule([self._build_test_match()], followed, days)))
            return f"已发送测试日报（{days} 天）"
        if action == "全部":
            messages = []
            for item in ("赛前", "赛果", "变更", "开幕", "日报"):
                messages.append(await self._run_test_push(item, days))
            return "；".join(messages)
        raise ValueError(f"unknown test action: {action}")

    @filter.command("cs比赛")
    async def cmd_list(self, event: AstrMessageEvent) -> MessageEventResult:
        followed = self.store.get_followed_team_names()
        if not self._scheduled:
            return event.plain_result("📭 当前没有已安排的比赛\n发送 ~cs刷新 更新日程")
        return event.plain_result(fmt_schedule(self._scheduled, followed))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs刷新")
    async def cmd_refresh(self, event: AstrMessageEvent) -> MessageEventResult:
        self._create_background_task(self._fetch_and_schedule(), "cmd_refresh")
        return event.plain_result("✅ 正在刷新日程，发送 ~cs比赛 查看最新赛程")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs设置群")
    async def cmd_add_group(self, event: AstrMessageEvent, gid: str = "") -> MessageEventResult:
        gid = gid.strip()
        if not gid.isdigit():
            return event.plain_result("用法：~cs设置群 <群号>\n例：~cs设置群 123456789")
        if self.store.add_group(gid):
            return event.plain_result(f"✅ 已添加推送群：{gid}")
        return event.plain_result(f"ℹ️ 群 {gid} 已在推送列表中")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs移除群")
    async def cmd_remove_group(self, event: AstrMessageEvent, gid: str = "") -> MessageEventResult:
        gid = gid.strip()
        if self.store.remove_group(gid):
            return event.plain_result(f"✅ 已移除推送群：{gid}")
        return event.plain_result(f"ℹ️ 群 {gid} 不在推送列表中")

    @filter.command("cs群列表")
    async def cmd_group_list(self, event: AstrMessageEvent) -> MessageEventResult:
        groups = self.store.get_groups()
        if not groups:
            return event.plain_result("📭 当前没有推送群\n使用 ~cs设置群 <群号> 添加")
        return event.plain_result("📢 当前推送群：\n" + "\n".join(f"  · {g}" for g in groups))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs提醒")
    async def cmd_remind(self, event: AstrMessageEvent, minutes: str = "") -> MessageEventResult:
        try:
            m = int(minutes.strip())
            if not 1 <= m <= 120:
                raise ValueError
        except (ValueError, AttributeError):
            cur = self.store.get_remind_minutes()
            return event.plain_result(f"当前赛前提醒：{cur} 分钟\n修改：~cs提醒 <分钟>（1~120）")
        self.store.set_remind_minutes(m)
        for mid, task in list(self._match_tasks.items()):
            if not task.done():
                task.cancel()
                self.store.clear_upcoming_notified(mid)
        self._match_tasks.clear()
        self._scheduled_mids.clear()
        self._create_background_task(self._fetch_and_schedule(), "cmd_remind")
        return event.plain_result(f"✅ 已设置：比赛开始前 {m} 分钟推送提醒，已自动重建所有比赛提醒")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs关注")
    async def cmd_follow(self, event: AstrMessageEvent, name: str = "") -> MessageEventResult:
        """QQ 指令关注：通过名称搜索并关注（建议使用 Web 面板精确搜索）"""
        name = name.strip()
        if not name:
            return event.plain_result(
                "用法：~cs关注 <战队名>\n"
                "例：~cs关注 NaVi\n"
                "⚠️ 建议通过 Web 面板搜索关注，可精确选择避免名称歧义"
            )
        # 通过 API 搜索战队，取第一个精确匹配
        results = await self.client.search_teams(name, per_page=10)
        if not results:
            return event.plain_result(f"❌ 未找到战队：{name}\n请检查拼写或通过 Web 面板搜索")

        # 优先精确匹配，再取第一个
        exact = next((t for t in results if t.get("name", "").lower() == name.lower()), None)
        team  = exact or results[0]
        tid   = team.get("id")
        tname = team.get("name", name)
        slug  = team.get("slug", "")

        if self.store.follow_team(tid, tname, slug):
            self._create_background_task(self._fetch_and_schedule(), "cmd_follow")
            msg = f"✅ 已关注：{tname}（ID: {tid}）\n正在刷新日程，该战队的赛事将自动加入推送"
            if not exact and len(results) > 1:
                others = "、".join(t.get("name","") for t in results[1:4])
                msg += f"\n\n💡 搜索还匹配到：{others}\n如需精确选择请使用 Web 面板"
            return event.plain_result(msg)
        return event.plain_result(f"ℹ️ 已经关注过 {tname} 了")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs取消")
    async def cmd_unfollow(self, event: AstrMessageEvent, name: str = "") -> MessageEventResult:
        name = name.strip()
        if not name:
            teams = self.store.get_followed_teams()
            if not teams:
                return event.plain_result("当前没有关注任何战队")
            lines = "\n".join(f"  {i+1}. {t['name']} (ID:{t['id']})" for i, t in enumerate(teams))
            return event.plain_result(f"用法：~cs取消 <战队名>\n\n当前关注：\n{lines}")

        # 按名称在关注列表里找（支持部分匹配）
        teams = self.store.get_followed_teams()
        matched = [t for t in teams if name.lower() in t.get("name","").lower()]
        if not matched:
            return event.plain_result(f"ℹ️ 没有关注过包含「{name}」的战队")
        if len(matched) > 1:
            lines = "\n".join(f"  · {t['name']} (ID:{t['id']})" for t in matched)
            return event.plain_result(f"找到多个匹配，请使用 Web 面板精确取消关注：\n{lines}")

        team = matched[0]
        if self.store.unfollow_team(team["id"]):
            self._create_background_task(self._fetch_and_schedule(), "cmd_unfollow")
            return event.plain_result(
                f"✅ 已取消关注：{team['name']}\n正在刷新日程，该战队独有赛事将从推送中移除"
            )
        return event.plain_result(f"ℹ️ 取消关注失败")

    @filter.command("cs关注列表")
    async def cmd_follow_list(self, event: AstrMessageEvent) -> MessageEventResult:
        teams = self.store.get_followed_teams()
        if not teams:
            return event.plain_result("📭 还没有关注任何战队\n使用 ~cs关注 <战队名> 或 Web 面板添加")
        lines = "\n".join(
            f"  · {t['name']}" + (f" (ID:{t['id']})" if t.get('id') else " (旧版)")
            for t in teams
        )
        return event.plain_result(f"⭐ 已关注的战队（{len(teams)} 支）：\n{lines}")

    @filter.command("cs状态")
    async def cmd_status(self, event: AstrMessageEvent) -> MessageEventResult:
        groups     = self.store.get_groups()
        remind     = self.store.get_remind_minutes()
        tiers      = self.store.get_min_tiers()
        teams      = self.store.get_followed_teams()
        ta_enabled = self.store.get("tournament_announce_enabled", True)
        ta_hours   = self.store.get("tournament_announce_hours", 2)
        image_push = self.store.get("image_push_enabled", True)
        test_mode  = self._is_test_mode_enabled()
        test_target = self._describe_test_target()

        group_str  = "\n".join(f"  · {g}" for g in groups) if groups else "  （未配置）"
        follow_str = "、".join(t["name"] for t in teams) if teams else "（无）"
        tier_str   = "、".join(t.upper() for t in tiers)
        ta_str     = f"✅ 开启（提前 {ta_hours} 小时）" if ta_enabled else "❌ 关闭"
        image_str  = "🖼️ 图片" if image_push else "📝 文字"
        test_str   = "✅ 已开启" if test_mode else "❌ 已关闭"

        return event.plain_result(
            "⚙️ 【CS2 推送插件状态】\n"
            "━━━━━━━━━━━━━\n"
            f"📢 推送群：\n{group_str}\n"
            f"🕐 赛前提醒：{remind} 分钟前\n"
            f"🏅 推送等级：{tier_str} 级\n"
            f"⭐ 关注战队：{follow_str}\n"
            f"📅 已安排比赛：{len(self._scheduled)} 场\n"
            f"🎉 赛事开幕通知：{ta_str}\n"
            f"🧾 推送样式：{image_str}\n"
            f"🧪 测试模式：{test_str}\n"
            f"🎯 测试目标：{test_target}\n"
            f"🔄 自动刷新：每 {self.store.get('fetch_interval_min') or self._fetch_interval} 分钟一次\n"
            f"🌐 Web 面板：{self._web_panel_url()}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs延迟通知")
    async def cmd_reschedule_notify(self, event: AstrMessageEvent, arg: str = "") -> MessageEventResult:
        arg = arg.strip()
        if arg in ("开", "on", "1", "true"):
            self.store.set_reschedule_notify(True)
            return event.plain_result("✅ 已开启延迟通知")
        elif arg in ("关", "off", "0", "false"):
            self.store.set_reschedule_notify(False)
            return event.plain_result("✅ 已关闭延迟通知")
        else:
            status = "开启" if self.store.get_reschedule_notify() else "关闭"
            return event.plain_result(
                f"当前延迟通知：{status}\n开启：~cs延迟通知 开\n关闭：~cs延迟通知 关"
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    @filter.command("cs面板")
    async def cmd_panel(self, event: AstrMessageEvent) -> MessageEventResult:
        token = str(self.store.get("web_panel_token") or "").strip()
        return event.plain_result(
            f"🌐 Web 管理面板\n"
            f"━━━━━━━━━━━━━\n"
            f"地址：{self._web_panel_url()}\n"
            f"带令牌地址：{self._web_panel_url(include_token=True)}\n"
            f"管理令牌：{token}\n"
            f"（非本机访问需要令牌，请勿公开分享）"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs测试")
    async def cmd_test_mode(self, event: AstrMessageEvent, arg: str = "") -> MessageEventResult:
        arg = arg.strip()
        enabled = self._is_test_mode_enabled()

        if not arg:
            status = "✅ 已开启" if enabled else "❌ 已关闭"
            target = self._describe_test_target()
            return event.plain_result(
                "🧪 【测试模式】\n"
                "━━━━━━━━━━━━━\n"
                f"状态：{status}\n"
                f"目标：{target}\n"
                "说明：测试模式下可手动发送模拟推送，不影响正常赛程逻辑\n"
                "配置：请在 Web 面板高级设置中填写测试 QQ 或群号\n"
                "指令：\n"
                "  ~cs测试 开            开启测试模式\n"
                "  ~cs测试 关            关闭测试模式\n"
                "  ~cs测试 赛前          发送测试赛前提醒\n"
                "  ~cs测试 赛果          发送测试赛果推送\n"
                "  ~cs测试 变更          发送测试赛程变更\n"
                "  ~cs测试 开幕          发送测试赛事开幕通知\n"
                "  ~cs测试 日报 [天数]   发送测试日报\n"
                "  ~cs测试 全部          依次发送一套完整测试消息"
            )

        parts = arg.split()
        action = parts[0]
        if action in ("撤回", "回收"):
            return event.plain_result("未知测试动作，发送 ~cs测试 查看帮助")
        if action in ("开", "on", "1", "true"):
            self._set_test_mode_enabled(True)
            return event.plain_result("✅ 测试模式已开启")
        if action in ("关", "off", "0", "false"):
            self._set_test_mode_enabled(False)
            return event.plain_result("✅ 测试模式已关闭")

        if not enabled:
            return event.plain_result("⚠️ 测试模式未开启，请先发送 ~cs测试 开")
        if not self._get_test_target():
            return event.plain_result("📭 尚未配置测试目标，请先在 Web 面板高级设置中填写测试 QQ 或群号")

        days = 1
        if action == "日报" and len(parts) > 1:
            try:
                days = max(1, min(int(parts[1]), 7))
            except ValueError:
                return event.plain_result("格式错误，示例：~cs测试 日报 2")

        valid = {"赛前", "赛果", "变更", "开幕", "日报", "全部", "撤回", "回收"}
        if action not in valid:
            return event.plain_result("未知测试动作，发送 ~cs测试 查看帮助")

        msg = await self._run_test_push(action, days, event)
        return event.plain_result(f"✅ {msg}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs推送")
    async def cmd_push_now(self, event: AstrMessageEvent, arg: str = "") -> MessageEventResult:
        """立即推送赛程到所有群"""
        try:
            days = int(arg.strip()) if arg.strip() else 1
            days = max(1, min(days, 7))
        except ValueError:
            days = 1
        if not self._scheduled:
            return event.plain_result("📭 当前没有已安排的比赛，请先发送 ~cs刷新")
        if not self._has_daily_matches_to_push(days):
            return event.plain_result(f"📭 未来 {days} 天内没有可推送的已安排比赛，已跳过日报推送")
        self._create_background_task(self._do_instant_push(days), "cmd_push_now")
        return event.plain_result(f"✅ 已向所有推送群发送 {days} 天内的赛程日报")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs日报")
    async def cmd_daily_push(self, event: AstrMessageEvent, arg: str = "") -> MessageEventResult:
        """配置或查看每日定时推送"""
        arg = arg.strip()

        # /cs日报 → 显示当前配置
        if not arg:
            enabled    = self.store.get("daily_push_enabled", False)
            times      = self.store.get("daily_push_times") or ["08:00"]
            days       = self.store.get("daily_push_days") or 1
            status_str = "✅ 开启" if enabled else "❌ 关闭"
            times_str  = "、".join(times)
            return event.plain_result(
                f"📅 【每日定时推送配置】\n"
                f"━━━━━━━━━━━━━\n"
                f"状态：{status_str}\n"
                f"推送时间：{times_str}（CST）\n"
                f"推送范围：{days} 天内的赛程\n"
                f"━━━━━━━━━━━━━\n"
                f"指令：\n"
                f"  ~cs日报 开         开启定时推送\n"
                f"  ~cs日报 关         关闭定时推送\n"
                f"  ~cs日报 时间 08:00 20:00   设置推送时间（可多个）\n"
                f"  ~cs日报 天数 2     设置推送几天内的赛程\n"
                f"  ~cs日报 预览       预览今日推送内容"
            )

        # /cs日报 开/关
        if arg in ("开", "on"):
            self.store.set("daily_push_enabled", True)
            times = "、".join(self.store.get("daily_push_times") or ["08:00"])
            return event.plain_result(f"✅ 每日定时推送已开启\n推送时间：{times}（CST）")

        if arg in ("关", "off"):
            self.store.set("daily_push_enabled", False)
            return event.plain_result("✅ 每日定时推送已关闭")

        # /cs日报 预览
        if arg == "预览":
            days     = int(self.store.get("daily_push_days") or 1)
            followed = self.store.get_followed_team_names()
            text     = fmt_daily_schedule(self._scheduled, followed, days)
            return event.plain_result(text)

        # /cs日报 时间 HH:MM [HH:MM ...]
        if arg.startswith("时间"):
            parts = arg.split()[1:]
            valid = []
            for t in parts:
                try:
                    h, m = t.split(":")
                    assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
                    valid.append(f"{int(h):02d}:{int(m):02d}")
                except Exception:
                    pass
            if not valid:
                return event.plain_result("格式错误，示例：~cs日报 时间 08:00 20:00")
            self.store.set("daily_push_times", valid)
            return event.plain_result(f"✅ 已设置推送时间：{'、'.join(valid)}（CST）")

        # /cs日报 天数 N
        if arg.startswith("天数"):
            parts = arg.split()
            try:
                n = int(parts[1])
                assert 1 <= n <= 7
            except Exception:
                return event.plain_result("格式错误，示例：~cs日报 天数 2（1~7）")
            self.store.set("daily_push_days", n)
            return event.plain_result(f"✅ 已设置推送范围：{n} 天内的赛程")

        return event.plain_result("未知参数，发送 ~cs日报 查看帮助")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("cs赛事提醒")
    async def cmd_tournament_announce(self, event: AstrMessageEvent, arg: str = "") -> MessageEventResult:
        """查看或设置赛事开幕提前推送时间"""
        arg = arg.strip()

        # 无参数 → 显示当前状态
        if not arg:
            enabled = self.store.get("tournament_announce_enabled", True)
            hours   = self.store.get("tournament_announce_hours", 2)
            status  = "✅ 开启" if enabled else "❌ 关闭"
            tiers   = "、".join(t.upper() for t in self.store.get_min_tiers())
            return event.plain_result(
                f"🎉 【赛事开幕通知配置】\n"
                f"━━━━━━━━━━━━━\n"
                f"状态：{status}\n"
                f"提前推送：开赛前 {hours} 小时\n"
                f"推送等级：{tiers} 级（关注战队不限等级）\n"
                f"━━━━━━━━━━━━━\n"
                f"指令：\n"
                f"  ~cs赛事提醒 开        开启赛事开幕推送\n"
                f"  ~cs赛事提醒 关        关闭赛事开幕推送\n"
                f"  ~cs赛事提醒 <小时>    设置提前推送时间（1~24）"
            )

        if arg in ("开", "on"):
            self.store.set("tournament_announce_enabled", True)
            hours = self.store.get("tournament_announce_hours", 2)
            return event.plain_result(f"✅ 赛事开幕通知已开启，将在开赛前 {hours} 小时推送")

        if arg in ("关", "off"):
            self.store.set("tournament_announce_enabled", False)
            return event.plain_result("✅ 赛事开幕通知已关闭")

        try:
            h = int(arg)
            if not 1 <= h <= 24:
                raise ValueError
        except ValueError:
            return event.plain_result("格式错误，示例：~cs赛事提醒 2（1~24，单位小时）")

        self.store.set("tournament_announce_hours", h)
        # 清理已安排的旧任务，让新设置生效
        for tid, task in list(self._tournament_tasks.items()):
            if not task.done():
                task.cancel()
        self._tournament_tasks.clear()
        self._create_background_task(self._check_tournament_announces(), "cmd_tournament_announce")
        return event.plain_result(f"✅ 已设置：赛事开始前 {h} 小时推送开幕通知，已重建提醒任务")

    @filter.command("cs帮助")
    async def cmd_help(self, event: AstrMessageEvent) -> MessageEventResult:
        return event.plain_result(
            "📖 【CS2 推送插件 指令列表】\n"
            "唤醒词：~ 或 _（两者等效）\n"
            "━━━━━━━━━━━━━\n"
            "【比赛查询】\n"
            "  ~cs比赛              查看已安排的比赛\n"
            "  ~cs刷新              立即刷新日程\n"
            "\n【推送设置】\n"
            "  ~cs推送 [天数]       立即推送赛程日报到所有群\n"
            "  ~cs日报              查看/配置每日定时推送\n"
            "  ~cs日报 开/关        开启或关闭定时推送\n"
            "  ~cs日报 时间 HH:MM   设置推送时间（可多个）\n"
            "  ~cs日报 天数 N       设置推送几天内赛程\n"
            "  ~cs日报 预览         预览推送内容\n"
            "\n【赛事开幕通知】\n"
            "  ~cs赛事提醒          查看赛事开幕通知配置\n"
            "  ~cs赛事提醒 开/关    开启或关闭赛事开幕通知\n"
            "  ~cs赛事提醒 <小时>   设置提前推送时间（1~24）\n"
            "\n【战队关注】\n"
            "  ~cs关注 <队名>       关注战队（任何级别都推）\n"
            "  ~cs取消 <队名>       取消关注\n"
            "  ~cs关注列表          查看已关注战队\n"
            "\n【群组管理】\n"
            "  ~cs设置群 <群号>     添加推送群\n"
            "  ~cs移除群 <群号>     移除推送群\n"
            "  ~cs群列表            查看推送群\n"
            "\n【其他设置】\n"
            "  ~cs提醒 <分钟>       设置赛前提醒时间（1~120）\n"
            "  ~cs延迟通知 开/关    开关赛程变更通知\n"
            "  ~cs测试              查看测试模式与模拟推送命令\n"
            "  ~cs测试 开/关        开启或关闭测试模式\n"
            "  ~cs测试 赛前/赛果/变更/开幕/日报/全部          发送测试消息\n"
            "  ~cs状态              查看当前配置\n"
            "  ~cs面板              查看 Web 管理面板地址\n"
            "  ~cs帮助              显示此帮助\n"
            "━━━━━━━━━━━━━\n"
            f"🌐 Web 管理面板：{self._web_panel_url()}"
        )
