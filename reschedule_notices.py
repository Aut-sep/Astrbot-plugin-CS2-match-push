"""Filter and batch schedule changes without changing reminder/result tracking."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib

from .bracket_data import bracket_team_name
from .formatters import fmt_reschedule, fmt_time, parse_dt, sched_str
from .group_rules import Rules, scope, current as current_rules
from .match_stages import match_stage_label

WINDOW_SECONDS = 2 * 60 * 60
BATCH_THRESHOLD = 3


def schedule_changed(old, new):
    try:
        return parse_dt(old) != parse_dt(new)
    except (TypeError, ValueError):
        return old != new


def teams_confirmed(match):
    teams = [slot.get('opponent') or {} for slot in match.get('opponents') or []]
    unknown = {'', 'tbd', 'to be determined', 'unknown', '待定', '待公布'}
    return len(teams) == 2 and all(
        str(team.get('name') or '').strip().casefold() not in unknown for team in teams
    )


def eligible(match, old_time, now, reminded=False):
    if match.get('status') != 'not_started' or not teams_confirmed(match):
        return False
    try:
        old, new = parse_dt(old_time), parse_dt(sched_str(match))
    except (TypeError, ValueError):
        return False
    # Sub-minute precision/zone formatting changes do not merit a visible notice.
    if old.replace(second=0, microsecond=0) == new.replace(second=0, microsecond=0) or new <= now:
        return False
    return reminded or any(0 < (start - now).total_seconds() <= WINDOW_SECONDS for start in (old, new))


@dataclass
class Change:
    match: dict
    old_time: str
    new_time: str
    reminded: bool
    reminded_groups: tuple = ()
    audience: tuple = ()

    @property
    def key(self):
        return f"reschedule:{self.match['id']}:{parse_dt(self.new_time).isoformat()}"


def group_eligible(plugin, change, gid, now):
    rules = Rules(plugin.store, gid)
    return (gid in plugin.store.get_groups() and gid in change.audience
            and rules.enabled('reschedule') and rules.accepts(change.match)
            and eligible(change.match, change.old_time, now, gid in change.reminded_groups))


def queue_change(plugin, match, old_time, now):
    mid = match['id']
    receipts = plugin.store.get_delivery_targets(f'upcoming:{mid}')
    reminded_groups = {gid for gid in plugin.store.get_groups() if f'group:{gid}' in receipts}
    # Old installations may have only a completed broadcast marker.
    if not receipts and plugin.store.is_upcoming_notified(mid):
        reminded_groups.update(plugin.store.get_groups())
    previous = plugin._pending_reschedules.pop(mid, None)
    if previous:
        reminded_groups.update(previous.reminded_groups)
        plugin.store.clear_delivery(previous.key)
    change = Change(deepcopy(match), old_time, sched_str(match), bool(reminded_groups),
                    tuple(reminded_groups), tuple(plugin.store.get_groups()))
    recipients = [gid for gid in change.audience if group_eligible(plugin, change, gid, now)]
    if recipients:
        change.audience = tuple(recipients)
        plugin._pending_reschedules[mid] = change


def ordered(changes):
    return sorted(changes, key=lambda item: (
        (item.match.get('league') or {}).get('name') or '未知联赛',
        parse_dt(item.new_time), str(item.match['id']),
    ))


def summary_text(changes):
    lines = ['【CS2 赛程变更汇总】', f'共 {len(changes)} 场 · 北京时间']
    league = None
    for change in ordered(changes):
        current = (change.match.get('league') or {}).get('name') or '未知联赛'
        if current != league:
            lines.extend(['', f'赛事：{current}'])
            league = current
        names = [bracket_team_name(slot.get('opponent') or {}) for slot in change.match['opponents']]
        lines.extend([f'{names[0]} vs {names[1]}',
                      *([f'阶段：{stage}'] if (stage := match_stage_label(change.match)) else []),
                      f'{fmt_time(change.old_time)} → {fmt_time(change.new_time)}'])
    return '\n'.join(lines)


async def components(plugin, changes):
    changes = [replace(change,match=plugin._match_with_snapshot_names(change.match)) for change in changes]
    if len(changes) < BATCH_THRESHOLD:
        change = changes[0]
        return await plugin._notification_components(
            plugin._format(fmt_reschedule, change.match, fmt_time(change.old_time), fmt_time(change.new_time)),
            match=change.match,
        )
    matches = [change.match for change in changes]
    marks = await plugin._resolve_team_marks(plugin._notice_teams(matches=matches))
    if not current_rules(plugin.store).get('image_push_enabled', True):
        return await plugin._team_text_components(summary_text(changes), marks)
    path = await asyncio.to_thread(plugin._banner_renderer.render_reschedules,
                                   ordered(changes), plugin._new_notice_path(), marks)
    _, _, Image = plugin._message_component_types()
    image = plugin._make_image_file_component(Image, path)
    if image is None:
        raise RuntimeError('赛程变更汇总图片生成失败')
    return [image]


async def send_pending(plugin, matches, now, *, unverified=()):
    current = {match['id']: match for match in matches}
    for mid, change in list(plugin._pending_reschedules.items()):
        if mid in unverified:
            continue
        latest = current.get(mid)
        if (not latest or plugin.store.is_finished_notified(mid)
                or schedule_changed(change.new_time, sched_str(latest))):
            plugin._pending_reschedules.pop(mid, None)
            plugin.store.clear_delivery(change.key)
        else:
            change.match = deepcopy(latest)
    changes = ordered(change for mid, change in plugin._pending_reschedules.items() if mid not in unverified)
    rendered = {}
    for group in plugin.store.get_groups():
        target = f'group:{group}'
        waiting = [change for change in changes if group_eligible(plugin, change, group, now)
                   and target not in plugin.store.get_delivery_targets(change.key)]
        batches = [waiting] if len(waiting) >= BATCH_THRESHOLD else [[change] for change in waiting]
        for batch in batches:
            keys = tuple(change.key for change in batch)
            transport = keys[0] if len(batch) == 1 else 'reschedule-batch:' + hashlib.sha256(
                '\n'.join(keys).encode()).hexdigest()
            def gate():
                return all(group_eligible(plugin, change, group, now) for change in batch)
            async def deliver():
                cache_key = (group, keys)
                if cache_key not in rendered:
                    rendered[cache_key] = await components(plugin, batch)
                if not gate():
                    return []
                return await plugin._push_components_to_targets_with_refs([('group', group)], rendered[cache_key])
            with scope(Rules(plugin.store, group), gate=gate):
                sent = await plugin._deliver_notification(transport, deliver)
            if sent:
                for change in batch:
                    plugin.store.mark_delivery_target(change.key, target)
                if len(batch) >= BATCH_THRESHOLD:
                    plugin.store.clear_delivery(transport)
                plugin.panel.push_log('OK', f'已向群 {group} 发送赛程变更：{len(batch)} 场')
    for change in changes:
        active = [gid for gid in change.audience if group_eligible(plugin, change, gid, now)]
        if all(f'group:{gid}' in plugin.store.get_delivery_targets(change.key) for gid in active):
            plugin._pending_reschedules.pop(change.match['id'], None)
            plugin.store.clear_delivery(change.key)
