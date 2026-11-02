"""Read-only CS result queries using the panel's saved subscription filters."""

from datetime import timedelta

from .config import ConfigError, integer
from .result_helpers import final_score
from .formatters import CST, fmt_time, match_tier, now_utc, parse_dt, sched_str, team_name


def accepts_result(store, match):
    names = [team_name(match, index).lower() for index in range(2)]
    if any(word.lower() in name for word in store.get("blacklist_teams", []) for name in names):
        return False
    league = ((match.get("league") or {}).get("name") or "").lower()
    if any(word.lower() in league for word in store.get("blacklist_leagues", [])):
        return False
    ids = {str((slot.get("opponent") or {}).get("id")) for slot in match.get("opponents") or []}
    followed = any(
        str(team["id"]) in ids if team.get("id") is not None
        else str(team.get("name", "")).lower() in names
        for team in store.get_followed_teams()
    )
    return match_tier(match) in store.get_min_tiers() or (followed and store.get("notify_all_followed", True))


def format_results(matches, label):
    lines = [f"【CS2 最近赛果 · {label}】", "时间：北京时间 · 按计划开赛日期回看", ""]
    for match in sorted(matches, key=lambda item: (parse_dt(sched_str(item)), str(item["id"])), reverse=True):
        left, right = (team_name(match, index) for index in range(2))
        score = final_score(match)
        if score is not None:
            line = f"{left}  {score[0]} : {score[1]}  {right}"
        else:
            line = f"{left} vs {right} · 比分待确认"
        if match.get("forfeit"):
            winner = (match.get("winner") or {}).get("name")
            line += "（弃权" + (f"，胜者：{winner}" if winner else "") + "）"
        lines.append(line)
        league = (match.get("league") or {}).get("name") or "赛事待定"
        stage = (match.get("tournament") or {}).get("name") or ""
        lines.append("  " + " · ".join(filter(None, [fmt_time(sched_str(match)), league, stage, f"ID {match['id']}"])))
        lines.append("")
    if not matches:
        lines.append("暂无符合当前赛事级别、关注优先和黑名单条件的已结束比赛。")
    return "\n".join(lines).rstrip()


async def preview_results(plugin, days=3, match_id=""):
    days = integer(days, "days", 1, 30)
    if match_id:
        identity = integer(match_id, "match_id", 1)
        match = await plugin.client.get_match_result(identity)
        if not isinstance(match, dict) or str(match.get("id")) != str(identity):
            raise ConfigError("未获取到这场 CS 比赛，请检查比赛 ID 后重试")
        if match.get("status") != "finished":
            return {"text": "这场 CS 比赛尚未结束或已取消，暂无最终赛果。"}
        rows, label = [match], f"比赛 {identity}"
    else:
        now = now_utc()
        start = now.astimezone(CST).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        rows = await plugin.client.get_matches_between(start, now)
        if rows is None:
            raise ConfigError("CS 赛果获取失败，请稍后重试")
        rows = [match for match in rows if match.get("status") == "finished"]
        label = f"近 {days} 天"
    rows = [match for match in rows if accepts_result(plugin.store, match)]
    return {
        "text": format_results(rows, label),
        "note": "沿用 CS 板块已保存的赛事级别、关注优先和黑名单；查询只在网页展示，不触发群推送。",
    }
