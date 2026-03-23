"""
astrbot_plugin_cs2_match_push  v3.1.0
CS2 比赛推送插件

推送逻辑：
  - 每 N 分钟静默刷新一次日程（默认10分钟，WebUI 可配置）
  - 发现比赛时间变更时推送变更通知，并重新安排倒计时
  - 赛前 X 分钟精确推送单场提醒
  - 比赛结束后自动推送赛果

指令：
  /cs比赛            查看已安排的比赛列表
  /cs刷新            立即重新拉取日程（静默）
  /cs关注 <队名>     关注战队（任何级别都推）
  /cs取消 <队名>     取消关注
  /cs关注列表        查看已关注战队
  /cs提醒 <分钟>     设置赛前提醒时间
  /cs设置群 <群号>   添加推送群
  /cs移除群 <群号>   移除推送群
  /cs群列表          查看推送群
  /cs状态            查看当前配置
  /cs帮助            显示帮助
"""

import asyncio
import json
import os
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Optional

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api import logger

# ─────────────────────────────────────────
# 固定常量
# ─────────────────────────────────────────
RESULT_CHECK_INTERVAL = 300
API_BASE              = "https://api.pandascore.co"
CST                   = timezone(timedelta(hours=8))


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_cst() -> datetime:
    return datetime.now(CST)


def _fmt_time(iso: Optional[str]) -> str:
    if not iso:
        return "未知"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(CST).strftime("%m-%d %H:%M")
    except Exception:
        return iso


def _parse_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _team_name(match: dict, idx: int) -> str:
    try:
        return match["opponents"][idx]["opponent"]["name"]
    except (IndexError, KeyError, TypeError):
        return "TBD"


def _match_tier(match: dict) -> str:
    return ((match.get("tournament") or {}).get("tier") or "unranked").lower()


def _sched_str(match: dict) -> Optional[str]:
    return match.get("scheduled_at") or match.get("begin_at")


def _translate_match_name(name: str) -> str:
    """将比赛阶段名翻译为中文"""
    if not name:
        return name

    # 阶段关键词翻译
    translations = {
        "Grand final":              "总决赛",
        "Grand Final":              "总决赛",
        "Final":                    "决赛",
        "Semifinal":                "半决赛",
        "Semifinal 1":              "半决赛 1",
        "Semifinal 2":              "半决赛 2",
        "Quarterfinal":             "四分之一决赛",
        "Upper bracket final":      "上半区决赛",
        "Upper bracket semifinal":  "上半区半决赛",
        "Upper bracket quarterfinal": "上半区四分之一决赛",
        "Upper bracket round":      "上半区轮次",
        "Lower bracket final":      "下半区决赛",
        "Lower bracket semifinal":  "下半区半决赛",
        "Lower bracket quarterfinal": "下半区四分之一决赛",
        "Lower bracket round":      "下半区轮次",
        "Winners match":            "胜者组比赛",
        "Winners final":            "胜者组决赛",
        "Losers match":             "败者组比赛",
        "Losers final":             "败者组决赛",
        "Decider match":            "决定赛",
        "Elimination match":        "淘汰赛",
        "Group stage":              "小组赛",
        "Group Stage":              "小组赛",
        "Playoffs":                 "季后赛",
        "Play-in":                  "入围赛",
        "Round":                    "轮次",
        "match":                    "比赛",
    }

    result = name
    # 先替换长词组，避免短词把长词组切断
    for en, zh in sorted(translations.items(), key=lambda x: -len(x[0])):
        result = result.replace(en, zh)
    return result


# ─────────────────────────────────────────
# 数据持久化
# ─────────────────────────────────────────

class DataStore:
    _DEFAULTS = {
        "followed_teams":      [],
        "push_groups":         [],
        "remind_minutes":      10,
        "min_tiers":           ["s", "a"],
        "notified_upcoming":   [],
        "notified_finished":   [],
        "match_schedules":     {},
        "reschedule_notify":   True,   # 是否推送延迟通知
    }

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in self._DEFAULTS.items():
                    data.setdefault(k, v)
                return data
            except Exception:
                pass
        return dict(self._DEFAULTS)

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def follow_team(self, name: str) -> bool:
        key = name.lower().strip()
        if key not in self._data["followed_teams"]:
            self._data["followed_teams"].append(key)
            self.save()
            return True
        return False

    def unfollow_team(self, name: str) -> bool:
        key = name.lower().strip()
        if key in self._data["followed_teams"]:
            self._data["followed_teams"].remove(key)
            self.save()
            return True
        return False

    def get_followed_teams(self) -> list:
        return self._data["followed_teams"]

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

    def set_remind_minutes(self, m: int):
        self._data["remind_minutes"] = m
        self.save()

    def get_remind_minutes(self) -> int:
        return self._data.get("remind_minutes", 10)

    def get_min_tiers(self) -> list:
        return self._data.get("min_tiers", ["s", "a"])

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

    def is_finished_notified(self, mid: int) -> bool:
        return mid in self._data["notified_finished"]

    def mark_finished_notified(self, mid: int):
        if mid not in self._data["notified_finished"]:
            self._data["notified_finished"].append(mid)
            self._data["notified_finished"] = self._data["notified_finished"][-500:]
            self.save()

    def get_match_schedule(self, mid: int) -> Optional[str]:
        return self._data["match_schedules"].get(str(mid))

    def set_match_schedule(self, mid: int, sched: str):
        self._data["match_schedules"][str(mid)] = sched
        self.save()

    def get_reschedule_notify(self) -> bool:
        return self._data.get("reschedule_notify", True)

    def set_reschedule_notify(self, enabled: bool):
        self._data["reschedule_notify"] = enabled
        self.save()


# ─────────────────────────────────────────
# PandaScore API
# ─────────────────────────────────────────

class PandaScoreClient:
    def __init__(self, token: str):
        self.headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self._timeout = aiohttp.ClientTimeout(total=20)

    async def _request(self, url: str, params: dict) -> Optional[dict | list]:
        """带自动重试的请求，最多重试 3 次"""
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        url,
                        headers=self.headers,
                        params=params,
                        timeout=self._timeout
                    ) as r:
                        if r.status == 200:
                            return await r.json()
                        logger.warning(f"[CS] 请求失败: HTTP {r.status} {url}")
                        if r.status >= 500 and attempt < 2:
                            await asyncio.sleep(5)
                            continue
                        return None
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"[CS] 请求异常（第{attempt+1}次），5秒后重试: {type(e).__name__}")
                    await asyncio.sleep(5)
                else:
                    logger.error(f"[CS] 请求失败（已重试3次）: {type(e).__name__} {url}")
        return None

    async def get_upcoming_matches(self, per_page: int = 50) -> list:
        result = await self._request(
            f"{API_BASE}/csgo/matches/upcoming",
            {"per_page": per_page, "sort": "begin_at"}
        )
        return result if isinstance(result, list) else []

    async def get_match_result(self, match_id: int) -> Optional[dict]:
        """用 past 接口查结果（免费 Token 单场详情接口 403）"""
        result = await self._request(
            f"{API_BASE}/csgo/matches/past",
            {"filter[id]": match_id, "per_page": 1}
        )
        if isinstance(result, list) and result:
            return result[0]
        return None


# ─────────────────────────────────────────
# 消息格式化
# ─────────────────────────────────────────

def fmt_schedule(matches: list, followed: list) -> str:
    if not matches:
        return "📭 未来 48 小时内没有符合条件的 CS2 比赛"
    lines = [f"📅 【已安排的 CS2 赛程】共 {len(matches)} 场"]
    for m in matches:
        t1     = _team_name(m, 0)
        t2     = _team_name(m, 1)
        sched  = _fmt_time(_sched_str(m))
        league = (m.get("league") or {}).get("name", "?")
        num    = m.get("number_of_games", 0)
        t1l, t2l = t1.lower(), t2.lower()
        star   = "⭐ " if any(f in t1l or f in t2l for f in followed) else ""
        lines.append(
            f"━━━━━━━━━━━━━\n"
            f"🕒 {sched}\n"
            f"⚔️  {star}{t1}  vs  {t2}\n"
            f"📋 BO{num} · {league}"
        )
    return "\n".join(lines)


def fmt_upcoming(match: dict, remind_min: int) -> str:
    t1     = _team_name(match, 0)
    t2     = _team_name(match, 1)
    league = (match.get("league") or {}).get("name", "未知联赛")
    num    = match.get("number_of_games", 0)
    sched  = _fmt_time(_sched_str(match))
    stream = next(
        (s.get("raw_url", "") for s in match.get("streams_list", []) if s.get("main")),
        ""
    )
    lines = [
        f"⏰ 【CS2 赛前提醒】还有 {remind_min} 分钟开赛！",
        "━━━━━━━━━━━━━",
        f"🕒 {sched}",
        f"⚔️  {t1}  vs  {t2}",
        f"📋 BO{num} · {league}",
        "📺 直播：",
        "  🔴 斗鱼玩机器：https://www.douyu.com/6657",
        "  🔵 B站259：https://live.bilibili.com/1883358196",
    ]
    if stream:
        lines.append(f"  🌐 官方：{stream}")
    return "\n".join(lines)


def fmt_reschedule(match: dict, old_time: str, new_time: str) -> str:
    t1     = _team_name(match, 0)
    t2     = _team_name(match, 1)
    league = (match.get("league") or {}).get("name", "未知联赛")
    return (
        f"📢 【CS2 赛程变更】\n"
        f"━━━━━━━━━━━━━\n"
        f"⚔️  {t1}  vs  {t2}\n"
        f"🏆 {league}\n"
        f"🕒 原定：{old_time}\n"
        f"🕒 调整：{new_time}"
    )


def fmt_finished(match: dict) -> str:
    t1     = _team_name(match, 0)
    t2     = _team_name(match, 1)
    league = (match.get("league") or {}).get("name", "未知联赛")
    tour   = (match.get("tournament") or {}).get("name", "")
    name   = _translate_match_name(match.get("name", ""))

    score_map = {
        r["team_id"]: r["score"]
        for r in match.get("results", []) if "team_id" in r
    }

    def tid(i):
        try:
            return match["opponents"][i]["opponent"]["id"]
        except Exception:
            return None

    s1 = score_map.get(tid(0), "-")
    s2 = score_map.get(tid(1), "-")
    winner      = match.get("winner") or {}
    winner_name = winner.get("name", "未知")

    if match.get("draw"):
        result_str = "🤝 平局"
    elif match.get("forfeit"):
        result_str = f"🏳️ 弃权，胜者：{winner_name}"
    else:
        result_str = f"🏅 胜者：{winner_name}"

    id_to_name = {
        opp["opponent"]["id"]: opp["opponent"]["name"]
        for opp in match.get("opponents", []) if opp.get("opponent")
    }
    games_lines = []
    for g in match.get("games", []):
        if g.get("status") == "not_played":
            continue
        pos     = g.get("position", "?")
        gw_id   = (g.get("winner") or {}).get("id")
        gw_name = id_to_name.get(gw_id, "未知") if gw_id else "未知"
        forfeit = "（弃权）" if g.get("forfeit") else ""
        games_lines.append(f"  第{pos}局：{gw_name} 获胜{forfeit}")

    lines = [
        "🎉 【CS2 赛事结果】",
        "━━━━━━━━━━━━━",
        f"🏆 {league}" + (f" · {tour}" if tour else ""),
    ]
    if name:
        lines.append(f"📌 {name}")
    lines += [
        f"⚔️  {t1}  {s1} : {s2}  {t2}",
        result_str,
        "📊 各局详情：",
        "\n".join(games_lines) if games_lines else "  暂无详细数据",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────
# 插件主体
# ─────────────────────────────────────────

@register(
    "astrbot_plugin_cs2_match_push",
    "CS2 比赛推送插件",
    "每N分钟静默刷新，时间变更时提醒，精确倒计时推送赛前提醒和赛后结果",
    "3.1.0",
)
class CSMatchPlugin(Star):

    def __init__(self, context: Context, config=None):
        super().__init__(context, config)
        self._load_config(config)
        from astrbot.core.star.star_tools import StarTools
        data_dir  = StarTools.get_data_dir("astrbot_plugin_cs_match")
        data_file = str(data_dir / "cs_data.json")
        self.store  = DataStore(data_file)
        self.client = PandaScoreClient(self._token)
        self._scheduled: list  = []
        self._loop_task        = None
        self._match_tasks: dict   = {}
        self._scheduled_mids: set = set()  # 已安排过的比赛 id，防止重复创建协程
        self._notified_reschedule: dict = {}  # {mid: new_sched} 已推送变更通知的记录

    def _load_config(self, config=None):
        cfg = config or {}

        def _get(key, default):
            try:
                v = cfg.get(key)
                return v if v not in (None, "", [], {}) else default
            except Exception:
                return default

        self._token          = _get("pandascore_token", "")
        self._fetch_interval = int(_get("fetch_interval_min", 10))
        self._fetch_ahead    = int(_get("fetch_ahead_hours", 48))
        self._bo1_wait       = int(_get("bo1_wait_minutes", 30))
        self._bo3_wait       = int(_get("bo3_wait_minutes", 80))
        self._bo5_wait       = int(_get("bo5_wait_minutes", 120))
        # remind_minutes 和 min_tiers 由 QQ 指令管理，不从 WebUI 读取

        if not self._token:
            logger.warning("[CS] ⚠️ 未配置 PandaScore Token！请在 WebUI 插件配置中填写 pandascore_token")
        else:
            logger.info(f"[CS] 配置加载完成: 刷新间隔={self._fetch_interval}分钟, Token={self._token[:8]}...")

    async def initialize(self):
        self._loop_task = asyncio.create_task(self._poll_loop())
        logger.info(f"[CS] 插件已启动，每 {self._fetch_interval} 分钟自动刷新日程")

    async def destroy(self):
        if self._loop_task:
            self._loop_task.cancel()
        for t in self._match_tasks.values():
            t.cancel()
        self._match_tasks.clear()
        logger.info("[CS] 插件已停止")

    # ── 主轮询循环 ────────────────────────

    async def _poll_loop(self):
        await self._fetch_and_schedule()
        while True:
            try:
                await asyncio.sleep(self._fetch_interval * 60)
            except asyncio.CancelledError:
                break
            await self._fetch_and_schedule()

    async def _fetch_and_schedule(self):
        if not self._token:
            return

        matches = await self.client.get_upcoming_matches(per_page=50)
        if not matches:
            logger.warning("[CS] 未获取到比赛数据")
            return

        followed   = self.store.get_followed_teams()
        push_tiers = self.store.get_min_tiers()
        now        = _now_utc()
        cutoff     = now + timedelta(hours=self._fetch_ahead)

        to_schedule = []
        for m in matches:
            s = _sched_str(m)
            if not s:
                continue
            try:
                sched_dt = _parse_dt(s)
            except Exception:
                continue
            if not (now < sched_dt <= cutoff):
                continue
            tier = _match_tier(m)
            t1l  = _team_name(m, 0).lower()
            t2l  = _team_name(m, 1).lower()
            has_followed = any(f in t1l or f in t2l for f in followed)
            if tier in push_tiers or has_followed:
                to_schedule.append(m)

        self._scheduled = to_schedule
        remind_min = self.store.get_remind_minutes()

        for match in to_schedule:
            mid       = match["id"]
            new_sched = _sched_str(match)
            old_sched = self.store.get_match_schedule(mid)

            if old_sched and old_sched != new_sched:
                old_fmt = _fmt_time(old_sched)
                new_fmt = _fmt_time(new_sched)
                logger.info(f"[CS] 比赛 {mid} 时间变更：{old_fmt} -> {new_fmt}")
                # 无论是否推送通知，都要重建倒计时协程
                if mid in self._match_tasks and not self._match_tasks[mid].done():
                    self._match_tasks[mid].cancel()
                self.store.clear_upcoming_notified(mid)
                self._scheduled_mids.discard(mid)
                # 防止同一次变更重复推送通知
                last_notified = self._notified_reschedule.get(mid)
                if last_notified != new_sched:
                    self._notified_reschedule[mid] = new_sched
                    if self.store.get_reschedule_notify():
                        await self._push(fmt_reschedule(match, old_fmt, new_fmt))

            self.store.set_match_schedule(mid, new_sched)

            if self.store.is_finished_notified(mid):
                continue
            # 只要安排过就不再重复创建（时间变更时 clear_upcoming_notified 会同时从集合移除）
            if mid in self._scheduled_mids:
                continue

            t = asyncio.create_task(self._schedule_match(match, remind_min))
            self._match_tasks[mid] = t
            self._scheduled_mids.add(mid)
            logger.info(f"[CS] 已安排比赛 {mid} 的提醒任务")

    async def _schedule_match(self, match: dict, remind_min: int):
        mid = match["id"]
        try:
            await self._run_match(match, remind_min)
        finally:
            self._match_tasks.pop(mid, None)

    async def _run_match(self, match: dict, remind_min: int):
        mid      = match["id"]
        sched_dt = _parse_dt(_sched_str(match))

        # 赛前提醒
        remind_dt   = sched_dt - timedelta(minutes=remind_min)
        wait_remind = (remind_dt - _now_utc()).total_seconds()

        if wait_remind > 0 and not self.store.is_upcoming_notified(mid):
            logger.info(f"[CS] 比赛 {mid} 将在 {wait_remind/60:.1f} 分钟后推送提醒")
            try:
                await asyncio.sleep(wait_remind)
            except asyncio.CancelledError:
                logger.info(f"[CS] 比赛 {mid} 提醒任务取消（时间变更）")
                return
            if not self.store.is_upcoming_notified(mid):
                # 先标记再推送，防止竞争导致重复推送
                self.store.mark_upcoming_notified(mid)
                await self._push(fmt_upcoming(match, remind_min))
                logger.info(f"[CS] 已推送赛前提醒 比赛 {mid}")

        # 等赛后结果
        if self.store.is_finished_notified(mid):
            return

        wait_start = (sched_dt - _now_utc()).total_seconds()
        if wait_start > 0:
            try:
                await asyncio.sleep(wait_start)
            except asyncio.CancelledError:
                return

        num_games = match.get("number_of_games", 1) or 1
        if num_games >= 5:
            extra_wait = self._bo5_wait * 60
        elif num_games >= 3:
            extra_wait = self._bo3_wait * 60
        else:
            extra_wait = self._bo1_wait * 60

        logger.info(f"[CS] 比赛 {mid} BO{num_games}，{extra_wait//60} 分钟后开始查结果")
        try:
            await asyncio.sleep(extra_wait)
        except asyncio.CancelledError:
            return

        for _ in range(72):
            if self.store.is_finished_notified(mid):
                return
            result = await self.client.get_match_result(mid)
            if result and result.get("status") == "finished":
                # 先标记再推送，防止多个协程竞争导致重复推送
                if self.store.is_finished_notified(mid):
                    return
                self.store.mark_finished_notified(mid)
                await self._push(fmt_finished(result))
                logger.info(f"[CS] 已推送赛后结果 比赛 {mid}")
                return
            try:
                await asyncio.sleep(RESULT_CHECK_INTERVAL)
            except asyncio.CancelledError:
                return

        logger.warning(f"[CS] 比赛 {mid} 超时未结束，放弃等待")

    async def _push(self, text: str):
        groups = self.store.get_groups()
        if not groups:
            logger.warning("[CS] 未配置推送群")
            return
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.message.components import Plain
        from astrbot.core.star.star_tools import StarTools
        chain = MessageChain(chain=[Plain(text=text)])
        for gid in groups:
            try:
                await StarTools.send_message_by_id(
                    type="GroupMessage",
                    id=gid,
                    message_chain=chain,
                    platform="aiocqhttp"
                )
                logger.info(f"[CS] 已推送 -> 群 {gid}")
            except Exception as e:
                logger.error(f"[CS] 推送失败 群={gid}: {e}")

    # ── 指令 ──────────────────────────────

    @filter.command("cs比赛")
    async def cmd_list(self, event: AstrMessageEvent) -> MessageEventResult:
        followed = self.store.get_followed_teams()
        if not self._scheduled:
            return event.plain_result("📭 当前没有已安排的比赛\n发送 /cs刷新 更新日程")
        return event.plain_result(fmt_schedule(self._scheduled, followed))

    @filter.command("cs刷新")
    async def cmd_refresh(self, event: AstrMessageEvent) -> MessageEventResult:
        asyncio.create_task(self._fetch_and_schedule())
        return event.plain_result("✅ 正在刷新日程，发送 /cs比赛 查看最新赛程")

    @filter.command("cs设置群")
    async def cmd_add_group(self, event: AstrMessageEvent, gid: str = "") -> MessageEventResult:
        gid = gid.strip()
        if not gid.isdigit():
            return event.plain_result("用法：/cs设置群 <群号>\n例：/cs设置群 123456789")
        if self.store.add_group(gid):
            return event.plain_result(f"✅ 已添加推送群：{gid}")
        return event.plain_result(f"ℹ️ 群 {gid} 已在推送列表中")

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
            return event.plain_result("📭 当前没有推送群\n使用 /cs设置群 <群号> 添加")
        return event.plain_result("📢 当前推送群：\n" + "\n".join(f"  · {g}" for g in groups))

    @filter.command("cs提醒")
    async def cmd_remind(self, event: AstrMessageEvent, minutes: str = "") -> MessageEventResult:
        try:
            m = int(minutes.strip())
            if not 1 <= m <= 120:
                raise ValueError
        except (ValueError, AttributeError):
            cur = self.store.get_remind_minutes()
            return event.plain_result(f"当前赛前提醒：{cur} 分钟\n修改：/cs提醒 <分钟>（1~120）")
        self.store.set_remind_minutes(m)
        for mid, task in list(self._match_tasks.items()):
            if not task.done():
                task.cancel()
                self.store.clear_upcoming_notified(mid)
        self._match_tasks.clear()
        self._scheduled_mids.clear()
        asyncio.create_task(self._fetch_and_schedule())
        return event.plain_result(f"✅ 已设置：比赛开始前 {m} 分钟推送提醒，已自动重建所有比赛提醒")

    @filter.command("cs关注")
    async def cmd_follow(self, event: AstrMessageEvent, name: str = "") -> MessageEventResult:
        name = name.strip()
        if not name:
            return event.plain_result("用法：/cs关注 <战队名>\n例：/cs关注 NaVi")
        if self.store.follow_team(name):
            return event.plain_result(f"✅ 已关注：{name}")
        return event.plain_result(f"ℹ️ 已经关注过 {name} 了")

    @filter.command("cs取消")
    async def cmd_unfollow(self, event: AstrMessageEvent, name: str = "") -> MessageEventResult:
        name = name.strip()
        if not name:
            return event.plain_result("用法：/cs取消 <战队名>")
        if self.store.unfollow_team(name):
            return event.plain_result(f"✅ 已取消关注：{name}")
        return event.plain_result(f"ℹ️ 没有关注过 {name}")

    @filter.command("cs关注列表")
    async def cmd_follow_list(self, event: AstrMessageEvent) -> MessageEventResult:
        teams = self.store.get_followed_teams()
        if not teams:
            return event.plain_result("📭 还没有关注任何战队\n使用 /cs关注 <战队名> 添加")
        return event.plain_result("⭐ 已关注的战队：\n" + "\n".join(f"  · {t}" for t in teams))

    @filter.command("cs状态")
    async def cmd_status(self, event: AstrMessageEvent) -> MessageEventResult:
        groups   = self.store.get_groups()
        remind   = self.store.get_remind_minutes()
        tiers    = self.store.get_min_tiers()
        followed = self.store.get_followed_teams()

        group_str  = "\n".join(f"  · {g}" for g in groups) if groups else "  （未配置）"
        follow_str = "、".join(followed) if followed else "（无）"
        tier_str   = "、".join(t.upper() for t in tiers)

        return event.plain_result(
            "⚙️ 【CS2 推送插件状态】\n"
            "━━━━━━━━━━━━━\n"
            f"📢 推送群：\n{group_str}\n"
            f"🕐 赛前提醒：{remind} 分钟前\n"
            f"🏅 推送等级：{tier_str} 级\n"
            f"⭐ 关注战队：{follow_str}\n"
            f"📅 已安排比赛：{len(self._scheduled)} 场\n"
            f"🔄 自动刷新：每 {self._fetch_interval} 分钟一次"
        )

    @filter.command("cs延迟通知")
    async def cmd_reschedule_notify(self, event: AstrMessageEvent, arg: str = "") -> MessageEventResult:
        arg = arg.strip()
        if arg in ("开", "on", "1", "true"):
            self.store.set_reschedule_notify(True)
            return event.plain_result("✅ 已开启延迟通知，比赛时间变更时会推送提醒")
        elif arg in ("关", "off", "0", "false"):
            self.store.set_reschedule_notify(False)
            return event.plain_result("✅ 已关闭延迟通知，比赛时间变更时不会推送提醒")
        else:
            status = "开启" if self.store.get_reschedule_notify() else "关闭"
            return event.plain_result(
                f"当前延迟通知：{status}\n开启：/cs延迟通知 开\n关闭：/cs延迟通知 关"
            )

    @filter.command("cs帮助")
    @filter.command("cs帮助")
    async def cmd_help(self, event: AstrMessageEvent) -> MessageEventResult:
        return event.plain_result(
            "📖 【CS2 推送插件 指令列表】\n"
            "━━━━━━━━━━━━━\n"
            "/cs比赛              查看已安排的比赛\n"
            "/cs刷新              立即刷新日程（静默）\n"
            "/cs关注 <队名>       关注战队（所有级别都推）\n"
            "/cs取消 <队名>       取消关注\n"
            "/cs关注列表          查看已关注战队\n"
            "/cs提醒 <分钟>       设置赛前提醒时间\n"
            "/cs设置群 <群号>     添加推送群\n"
            "/cs移除群 <群号>     移除推送群\n"
            "/cs群列表            查看推送群\n"
            "/cs状态              查看当前配置\n"
            "/cs帮助              显示此帮助\n"
            "━━━━━━━━━━━━━\n"
            "每 N 分钟静默刷新，时间变更时自动通知\n"
            "赛前精确倒计时提醒，赛后自动推送结果"
        )