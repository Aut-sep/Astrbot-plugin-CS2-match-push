"""Filter and batch schedule changes without changing reminder/result tracking."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib

from .bracket_data import bracket_team_name
from .formatters import fmt_reschedule, fmt_time, parse_dt, sched_str

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

    @property
    def key(self):
        return f"reschedule:{self.match['id']}:{parse_dt(self.new_time).isoformat()}"


def queue_change(plugin, match, old_time, now):
    mid = match['id']
    reminded = plugin.store.is_upcoming_notified(mid) or bool(
        plugin.store.get_delivery_targets(f'upcoming:{mid}')
    )
    previous = plugin._pending_reschedules.pop(mid, None)
    if previous:
        reminded = reminded or previous.reminded
        plugin.store.clear_delivery(previous.key)
    # Eligibility is captured before rebuilding the reminder clears its receipt.
    if plugin.store.get_reschedule_notify() and eligible(match, old_time, now, reminded):
        plugin._pending_reschedules[mid] = Change(deepcopy(match), old_time, sched_str(match), reminded)


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
                      f'{fmt_time(change.old_time)} → {fmt_time(change.new_time)}'])
    return '\n'.join(lines)


async def components(plugin, changes):
    if len(changes) < BATCH_THRESHOLD:
        change = changes[0]
        return await plugin._notification_components(
            plugin._format(fmt_reschedule, change.match, fmt_time(change.old_time), fmt_time(change.new_time)),
            match=change.match,
        )
    matches = [change.match for change in changes]
    marks = await plugin._resolve_team_marks(plugin._notice_teams(matches=matches))
    if not plugin.store.get('image_push_enabled', True):
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
        if (not latest or not plugin.store.get_reschedule_notify()
                or plugin.store.is_finished_notified(mid)
                or schedule_changed(change.new_time, sched_str(latest))
                or not eligible(latest, change.old_time, now, change.reminded)):
            plugin._pending_reschedules.pop(mid, None)
            plugin.store.clear_delivery(change.key)
        else:
            change.match = deepcopy(latest)
    changes = ordered(change for mid, change in plugin._pending_reschedules.items() if mid not in unverified)
    if not changes:
        return
    groups = plugin.store.get_groups()
    rendered = {}
    for group in groups:
        target = f'group:{group}'
        waiting = [change for change in changes if target not in plugin.store.get_delivery_targets(change.key)]
        batches = [waiting] if len(waiting) >= BATCH_THRESHOLD else [[change] for change in waiting]
        for batch in batches:
            keys = tuple(change.key for change in batch)
            # Each match retains its own group receipt even if the batch changes.
            transport = keys[0] if len(batch) == 1 else 'reschedule-batch:' + hashlib.sha256(
                '\n'.join(keys).encode()).hexdigest()

            async def deliver():
                if keys not in rendered:
                    rendered[keys] = await components(plugin, batch)
                if not plugin.store.get_reschedule_notify() or group not in plugin.store.get_groups():
                    return []
                return await plugin._push_components_to_targets_with_refs([('group', group)], rendered[keys])

            if await plugin._deliver_notification(transport, deliver):
                for change in batch:
                    plugin.store.mark_delivery_target(change.key, target)
                if len(batch) >= BATCH_THRESHOLD:
                    plugin.store.clear_delivery(transport)
                plugin.panel.push_log('OK', f'已发送赛程变更：{len(batch)} 场' + ('（汇总）' if len(batch) >= BATCH_THRESHOLD else ''))
    for change in changes:
        if groups and all(f'group:{group}' in plugin.store.get_delivery_targets(change.key) for group in groups):
            mid = change.match['id']
            plugin._pending_reschedules.pop(mid, None)
            plugin.store.clear_delivery(change.key)
