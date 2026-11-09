"""Read-only CS result queries using the panel's saved subscription filters."""

import asyncio
import io
from datetime import timedelta

from astrbot.api import logger

from .config import ConfigError, integer
from .formatters import CST, fmt_time, now_utc, parse_dt, sched_str, team_name
from .group_rules import Rules
from .result_helpers import final_score


MAX_RESULT_IMAGE_MATCHES = 40


def _ordered(matches):
    return sorted(matches, key=lambda match: (parse_dt(sched_str(match)), str(match["id"])), reverse=True)


def _match_context(match):
    return (
        fmt_time(sched_str(match)),
        (match.get("league") or {}).get("name") or "赛事待定",
        (match.get("tournament") or {}).get("name") or "",
        f"ID {match['id']}",
    )


def _forfeit_note(match):
    if not match.get("forfeit"):
        return ""
    winner = (match.get("winner") or {}).get("name")
    return f"弃权，胜者：{winner}" if winner else "弃权"


def format_results(matches, label):
    lines = [f"【CS2 最近赛果 · {label}】", "时间：北京时间 · 按计划开赛日期回看", ""]
    for match in _ordered(matches):
        left, right = (team_name(match, index) for index in range(2))
        score = final_score(match)
        if score is not None:
            line = f"{left}  {score[0]} : {score[1]}  {right}"
        else:
            line = f"{left} vs {right} · 比分待确认"
        forfeit = _forfeit_note(match)
        if forfeit:
            line += f"（{forfeit}）"
        lines.append(line)
        lines.append("  " + " · ".join(filter(None, _match_context(match))))
        lines.append("")
    if not matches:
        lines.append("暂无符合当前赛事级别、关注优先和黑名单条件的已结束比赛。")
    return "\n".join(lines).rstrip()


def _render_result_image(plugin, matches, label, *, single_match):
    renderer = plugin._banner_renderer
    catalog = getattr(plugin, "team_logos", None)
    with io.BytesIO() as output:
        if single_match:
            match = matches[0]
            clock, league, stage, identity = _match_context(match)
            names = [team_name(match, index) for index in range(2)]
            score = final_score(match)
            text = "\n".join(filter(None, (
                " · ".join(filter(None, (league, stage))),
                f"计划开赛：{clock}（北京时间） · {identity}",
                _forfeit_note(match),
            )))
            renderer.render_notification(
                text, output, title=f"CS2 赛事结果 · {label}", team_names=names,
                logo_images=[catalog.get(name) if catalog else None for name in names],
                center_text=f"{score[0]} : {score[1]}" if score is not None else "比分待确认",
            )
        else:
            rows = []
            for match in _ordered(matches):
                clock, league, stage, identity = _match_context(match)
                score = final_score(match)
                names = [team_name(match, index) for index in range(2)]
                date, _, time = clock.partition(" ")
                rows.append({
                    "names": names,
                    "logos": [catalog.get(name) if catalog else None for name in names],
                    "date": date,
                    "time": time,
                    "score": f"{score[0]} : {score[1]}" if score is not None else "待确认",
                    "detail": " · ".join(filter(None, (league, stage, identity, _forfeit_note(match)))),
                })
            renderer.render_result_list(rows, label, output)
        return output.getvalue() or None


async def preview_results(plugin, days=3, match_id="", *, rules=None, include_image=False):
    days = integer(days, "days", 1, 30)
    if match_id:
        identity = integer(match_id, "match_id", 1)
        match = await plugin.client.get_match_result(identity)
        if not isinstance(match, dict) or str(match.get("id")) != str(identity):
            raise ConfigError("未获取到这场 CS 比赛，请检查比赛 ID 后重试")
        if match.get("status") != "finished":
            result = {"text": "这场 CS 比赛尚未结束或已取消，暂无最终赛果。"}
            if include_image:
                result["image"] = None
            return result
        rows, label = [match], f"比赛 {identity}"
    else:
        now = now_utc()
        start = now.astimezone(CST).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        rows = await plugin.client.get_matches_between(start, now)
        if rows is None:
            raise ConfigError("CS 赛果获取失败，请稍后重试")
        rows = [match for match in rows if match.get("status") == "finished"]
        label = f"近 {days} 天"
    selection = rules or plugin.store
    rules = selection if isinstance(selection, Rules) else Rules(selection)
    rows = [match for match in rows if rules.accepts(match)]
    result = {
        "text": format_results(rows, label),
        "note": "沿用选定的默认或群规则；查询只展示结果，不触发群推送。",
    }
    if include_image:
        result["image"] = None
        if rows and len(rows) <= MAX_RESULT_IMAGE_MATCHES and rules.get("image_push_enabled", True):
            try:
                result["image"] = await asyncio.to_thread(
                    _render_result_image, plugin, rows, label, single_match=bool(match_id),
                )
            except Exception as exc:
                logger.warning(f"[CS] 赛果图片生成失败：{type(exc).__name__}")
    return result
