"""
formatters.py — 消息格式化 & 工具函数
所有推送消息的文本组装逻辑，以及时间/字段解析工具
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

# ─────────────────────────────────────────
# 时区常量
# ─────────────────────────────────────────
CST = timezone(timedelta(hours=8))


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_cst() -> datetime:
    return datetime.now(CST)


def fmt_time(iso: Optional[str]) -> str:
    if not iso:
        return "未知"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(CST).strftime("%m-%d %H:%M")
    except Exception:
        return iso


def parse_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def team_name(match: dict, idx: int) -> str:
    fallback = ""
    if isinstance(match, dict):
        fallback = str(match.get("_t1" if idx == 0 else "_t2", "") or "").strip()
    try:
        name = str(match["opponents"][idx]["opponent"]["name"] or "").strip()
        if name:
            return name
    except (IndexError, KeyError, TypeError):
        pass
    return fallback or "TBD"


def match_tier(match: dict) -> str:
    return ((match.get("tournament") or {}).get("tier") or "unranked").lower()


def sched_str(match: dict) -> Optional[str]:
    return match.get("scheduled_at") or match.get("begin_at")


def is_push_ready_match(match: dict) -> bool:
    """Only include matches with both teams confirmed in schedule pushes."""
    return team_name(match, 0) != "TBD" and team_name(match, 1) != "TBD"


def followed_terms(followed: Optional[list]) -> list[str]:
    """Normalize followed teams for case-insensitive matching."""
    terms = []
    for item in followed or []:
        if isinstance(item, dict):
            name = item.get("name", "")
        else:
            name = str(item)
        name = name.strip().lower()
        if name:
            terms.append(name)
    return terms


def translate_match_stage(name: str) -> str:
    """Translate common bracket/stage names and drop duplicated matchup suffix."""
    if not name:
        return ""

    stage = re.split(r"\s*[:：]\s*", name, maxsplit=1)[0].strip()
    if not stage:
        return ""

    replacements = [
        ("grand final", "总决赛"),
        ("consolidation final", "季军赛"),
        ("lower bracket final", "败者组决赛"),
        ("upper bracket final", "胜者组决赛"),
        ("lower bracket semifinal", "败者组半决赛"),
        ("upper bracket semifinal", "胜者组半决赛"),
        ("lower bracket quarterfinal", "败者组四分之一决赛"),
        ("upper bracket quarterfinal", "胜者组四分之一决赛"),
        ("lower bracket round", "败者组第"),
        ("upper bracket round", "胜者组第"),
        ("round of 32", "三十二强"),
        ("round of 16", "十六强"),
        ("quarterfinal", "四分之一决赛"),
        ("semifinal", "半决赛"),
        ("final", "决赛"),
        ("group stage", "小组赛"),
        ("playoffs", "淘汰赛"),
        ("playoff", "淘汰赛"),
        ("swiss stage", "瑞士轮"),
        ("round robin", "循环赛"),
        ("opening match", "揭幕战"),
        ("opening matches", "揭幕战"),
        ("winners match", "胜者组比赛"),
        ("winner's match", "胜者组比赛"),
        ("losers match", "败者组比赛"),
        ("loser's match", "败者组比赛"),
        ("decider match", "出线战"),
        ("deciding match", "出线战"),
        ("elimination match", "淘汰战"),
        ("elimination", "淘汰赛"),
        ("lower bracket", "败者组"),
        ("upper bracket", "胜者组"),
    ]

    translated = stage
    for src, dst in replacements:
        translated = re.sub(src, dst, translated, flags=re.IGNORECASE)

    translated = re.sub(r"\bround\s+(\d+)\b", r"第\1轮", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\s+", " ", translated).strip()
    return translated


# ─────────────────────────────────────────
# 消息格式化
# ─────────────────────────────────────────

def fmt_schedule(matches: list, followed: list) -> str:
    """赛程列表（~cs比赛 指令）"""
    followed = followed_terms(followed)
    matches = [m for m in matches if is_push_ready_match(m)]
    if not matches:
        return "📭 未来 48 小时内没有符合条件的 CS2 比赛"
    lines = [f"📅 【已安排的 CS2 赛程】共 {len(matches)} 场"]
    for m in matches:
        t1    = team_name(m, 0)
        t2    = team_name(m, 1)
        sched = fmt_time(sched_str(m))
        lg    = (m.get("league") or {}).get("name", "?")
        num   = m.get("number_of_games", 0)
        lines.append(
            f"━━━━━━━━━━━━━\n"
            f"🕒 {sched}\n"
            f"⚔️  {t1}  vs  {t2}\n"
            f"📋 BO{num} · {lg}"
        )
    return "\n".join(lines)


def fmt_upcoming(match: dict, remind_min: int, custom_streams: list = None,
                 followed: list = None) -> str:
    """赛前提醒推送"""
    followed = followed_terms(followed)
    t1     = team_name(match, 0)
    t2     = team_name(match, 1)
    lg     = (match.get("league") or {}).get("name", "未知联赛")
    num    = match.get("number_of_games", 0)
    sched  = fmt_time(sched_str(match))
    stream = next(
        (s.get("raw_url", "") for s in match.get("streams_list", []) if s.get("main")),
        "",
    )

    # 是否涉及关注战队
    t1_starred = any(f in t1.lower() for f in followed)
    t2_starred = any(f in t2.lower() for f in followed)
    t1_str = t1
    t2_str = t2
    header = "【CS2 赛前提醒 · 关注战队】" if (t1_starred or t2_starred) else "⏰ 【CS2 赛前提醒】"

    lines = [
        f"{header}还有 {remind_min} 分钟开赛！",
        "━━━━━━━━━━━━━",
        f"🕒 {sched}",
        f"⚔️  {t1_str}  vs  {t2_str}",
        f"📋 BO{num} · {lg}",
        "📺 直播：",
    ]
    if stream:
        lines.append(f"  🌐 官方：{stream}")
    if custom_streams:
        for cs in custom_streams:
            lines.append(f"  📡 {cs.get('name','自定义')}：{cs.get('url','')}")
    return "\n".join(lines)


def fmt_reschedule(match: dict, old_time: str, new_time: str) -> str:
    """赛程时间变更通知"""
    t1 = team_name(match, 0)
    t2 = team_name(match, 1)
    lg = (match.get("league") or {}).get("name", "未知联赛")
    return (
        f"📢 【CS2 赛程变更】\n"
        f"━━━━━━━━━━━━━\n"
        f"⚔️  {t1}  vs  {t2}\n"
        f"🏆 {lg}\n"
        f"🕒 原定：{old_time}\n"
        f"🕒 调整：{new_time}"
    )


def fmt_finished(match: dict, followed: list = None) -> str:
    """赛事结果推送"""
    followed = followed_terms(followed)
    t1     = team_name(match, 0)
    t2     = team_name(match, 1)
    lg     = (match.get("league") or {}).get("name", "未知联赛")
    tour   = (match.get("tournament") or {}).get("name", "")
    name   = translate_match_stage(match.get("name", ""))

    # 关注战队标注
    t1_starred = any(f in t1.lower() for f in followed)
    t2_starred = any(f in t2.lower() for f in followed)
    t1_str = t1
    t2_str = t2
    header = "🎉 【CS2 赛事结果 · 关注战队】" if (t1_starred or t2_starred) else "🎉 【CS2 赛事结果】"

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
        header,
        "━━━━━━━━━━━━━",
        f"🏆 {lg}" + (f" · {tour}" if tour else ""),
    ]
    if name:
        lines.append(f"📌 {name}")
    lines += [
        f"⚔️  {t1_str}  {s1} : {s2}  {t2_str}",
        result_str,
        "📊 各局详情：",
        "\n".join(games_lines) if games_lines else "  暂无详细数据",
    ]
    return "\n".join(lines)

def fmt_tournament_announce(
    tour: dict,
    followed: list,
    announce_hours: int,
    is_followed_tour: bool = False,
) -> str:
    """赛事开幕通知推送"""
    name       = tour.get("name") or "未知赛事"
    followed = followed_terms(followed)
    tier       = (tour.get("tier") or "unranked").upper()
    lg         = (tour.get("league") or {}).get("name", "")
    begin_at   = fmt_time(tour.get("begin_at"))
    end_at     = fmt_time(tour.get("end_at"))
    location   = tour.get("country") or tour.get("location") or ""
    prize_pool = tour.get("prize_pool") or ""

    teams = []
    for opp in (tour.get("teams") or []):
        n = (opp or {}).get("name") if isinstance(opp, dict) else None
        if n:
            teams.append(n)

    if not teams:
        teams_str = "（待定）"
    else:
        teams_str = "、".join(t for t in teams)

    tier_icons = {"S": "🏆", "A": "🥇", "B": "🥈", "C": "🥉", "D": "📋"}
    tier_icon  = tier_icons.get(tier, "🎮")
    header     = "【关注战队参赛提醒】" if is_followed_tour else "🎉 【CS2 赛事开幕通知】"

    lines = [
        header,
        "━━━━━━━━━━━━━",
        f"{tier_icon} 赛事等级：{tier} 级",
        f"📌 赛事名称：{name}",
    ]
    if lg:
        lines.append(f"🏅 所属联赛：{lg}")
    lines.append(f"🕒 开赛时间：{begin_at}")
    if end_at and end_at != "未知":
        lines.append(f"🏁 预计结束：{end_at}")
    if location:
        lines.append(f"📍 举办地点：{location}")
    if prize_pool:
        lines.append(f"💰 奖金池：{prize_pool}")
    if teams:
        lines.append(f"⚔️  参赛战队：{teams_str}")
    if announce_hours > 0:
        lines.append(f"\n⏰ 距开赛还有约 {announce_hours} 小时，敬请期待！")
    return "\n".join(lines)


def fmt_daily_schedule(matches: list, followed: list, days: int) -> str:
    """每日定时推送：按天分组，只展示 days 天内的比赛"""
    followed = followed_terms(followed)
    now_cst_dt   = now_cst()
    day_buckets: dict[str, list] = {}

    for m in matches:
        if not is_push_ready_match(m):
            continue
        s = sched_str(m)
        if not s:
            continue
        try:
            dt_cst = parse_dt(s).astimezone(CST)
        except Exception:
            continue
        delta = (dt_cst.date() - now_cst_dt.date()).days
        if delta < 0 or delta >= days:
            continue
        day_label = "今天" if delta == 0 else ("明天" if delta == 1 else dt_cst.strftime("%m-%d"))
        day_buckets.setdefault(day_label, []).append((dt_cst, m))

    if not day_buckets:
        return f"📭 未来 {days} 天内没有符合条件的 CS2 比赛"

    header = f"📅 【CS2 赛程日报】{now_cst_dt.strftime('%m月%d日')} {now_cst_dt.strftime('%H:%M')} 播报"
    lines  = [header]

    for day_label, items in day_buckets.items():
        items.sort(key=lambda x: x[0])
        lines.append(f"\n🗓 {day_label}（共 {len(items)} 场）")
        for dt_cst, m in items:
            t1   = team_name(m, 0)
            t2   = team_name(m, 1)
            lg   = (m.get("league") or {}).get("name", "?")
            bo   = m.get("number_of_games", 0)
            lines.append(f"  {dt_cst.strftime('%H:%M')}  {t1} vs {t2}  BO{bo} · {lg}")

    return "\n".join(lines)
