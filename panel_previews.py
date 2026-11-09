"""Read-only management queries, sharing the existing chat query services."""

import asyncio
import base64

from .config import ConfigError, DEFAULT_SCHEDULE_DAYS, MAX_SCHEDULE_DAYS, integer, string
from .formatters import fmt_daily_schedule
from .group_rules import Rules


def require_fresh_schedule(plugin):
    schedule = plugin.get_panel_schedule_state()
    status = schedule["schedule_status"]
    if not plugin._token or status == "unconfigured":
        raise ConfigError("请先配置 PandaScore Token。")
    if status == "loading":
        raise ConfigError("正在获取赛程，请稍后重试。")
    if status == "error":
        if schedule["schedule_last_success_at"]:
            raise ConfigError("赛程更新失败，缓存已过期。请重新获取赛程。")
        raise ConfigError("赛程获取失败，请重新获取赛程。")


async def build_preview(plugin, query):
    kind = string(query.get("kind", "schedule"), "kind")
    if kind not in {"schedule", "daily", "bracket", "results"}:
        raise ConfigError("未知查询类型")
    # Keep the old API kind as an alias for clients that have not updated yet.
    if kind == "daily":
        kind = "schedule"
    if not plugin._token:
        raise ConfigError("请先配置 PandaScore Token。")

    gid = string(query.get('group_id', ''), 'group_id')
    if gid and gid not in plugin.store.get_groups():
        raise ConfigError('请选择已配置的接收群')
    rules = Rules(plugin.store, gid or None)
    if kind == "bracket":
        from .bracket_data import format_bracket

        stage = string(query.get("stage", "current"), "stage")
        allowed = {"current", "next", "all", "swiss", "knockout", "stage1", "stage2", "stage3"}
        if stage not in allowed:
            raise ConfigError("未知 CS Major 阶段")
        snapshot, stages, image = await plugin.bracket_images.get(stage, images_enabled=rules.get("image_push_enabled", True))
        upcoming = stage == "next"
        if stage == "current" and snapshot is not None and not stages:
            snapshot, stages, image = await plugin.bracket_images.get(
                "next", images_enabled=rules.get("image_push_enabled", True))
            upcoming = True
        if snapshot is None:
            raise ConfigError("对阵数据暂不可用，请稍后重试")
        if not stages:
            message = ("当前没有进行中的阶段，也没有已公布的下一阶段对阵。" if stage == "current"
                       else "下一阶段的对阵或开赛时间尚未公布。" if stage == "next"
                       else "该阶段的对阵尚未公布。")
            return {"text": snapshot["title"] + "：" + message}
        text = format_bracket(snapshot, stages)
        if upcoming:
            text = ("当前无进行中阶段，以下为下一阶段已公布对阵（尚未开赛）。\n" if stage == "current"
                    else "下一阶段已公布对阵（尚未开赛）。\n") + text
        return {
            "text": text,
            "image": base64.b64encode(image).decode("ascii") if image else None,
            "note": "沿用赛事对阵查询与图片缓存；预览不会向 QQ 发送消息。",
        }

    if kind == "results":
        match_id = string(query.get("match_id", ""), "match_id")
        if match_id:
            match_id = str(integer(match_id, "match_id", 1))
        from .cs_results import preview_results
        return await preview_results(plugin, query.get("days", 3), match_id, rules=rules)

    days = integer(query.get("days", DEFAULT_SCHEDULE_DAYS), "days", 1, MAX_SCHEDULE_DAYS)
    require_fresh_schedule(plugin)
    matches = [plugin._match_with_snapshot_names(match) for match in plugin._scheduled if rules.accepts(match)]
    available_days = min(days, plugin._fetch_ahead)
    note = f"群 {gid} · 已保存规则。" if gid else "使用默认规则。"
    if days > available_days:
        note += f"当前仅获取未来 {available_days} 天。"
    return {
        "text": fmt_daily_schedule(matches, rules.get_followed_team_names(), available_days),
        "note": note,
    }


async def timed_preview(plugin, query):
    try:
        return await asyncio.wait_for(build_preview(plugin, query), timeout=90)
    except asyncio.TimeoutError as exc:
        raise ConfigError("查询超时，请稍后重试") from exc
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError("赛事数据或图片暂不可用，请稍后重试") from exc
