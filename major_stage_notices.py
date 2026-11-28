"""Major stage reminders from the shared, freshly fetched bracket snapshot."""
import asyncio
from datetime import timedelta
from pathlib import Path

from .formatters import CST, now_utc
from .group_rules import groups_for
from .major_notifications import stage_context
from .tournament_events import _date


REMINDER_WINDOW = timedelta(hours=2)
KNOWN_STAGES = {'stage1', 'stage2', 'stage3', 'swiss', 'knockout'}
UNKNOWN_TEAMS = {'', 'tbd', 'to be determined', 'unknown', '待定', '待公布'}


def stage_start(stage):
    """Prefer the first published match time over placeholder stage dates."""
    matches = stage.get('matches') or []
    if (not stage.get('id') or stage.get('key') not in KNOWN_STAGES or not matches
            or stage.get('status') in {'finished', 'canceled', 'postponed'}):
        return None
    # Once any match has started, this is no longer a stage-opening reminder.
    # Unexpected statuses are also insufficient evidence for an upcoming stage.
    if any(match.get('status') not in {'not_started', 'canceled'} for match in matches):
        return None
    upcoming = [match for match in matches if match.get('status') == 'not_started']
    # Empty bracket placeholders alone do not constitute published pairings.
    # A published team may face TBD; keep the other slot unknown in the chart.
    if not any(
        str((slot.get('opponent') or {}).get('name') or '').strip().casefold() not in UNKNOWN_TEAMS
        for match in upcoming for slot in match.get('opponents') or []
    ):
        return None
    times = [date for match in upcoming
             if (date := (_date(match.get('scheduled_at')) or _date(match.get('begin_at')))) is not None]
    return min(times) if times else _date(stage.get('begin_at'))


def due_start(stage, now):
    start = stage_start(stage)
    return start if start is not None and now < start <= now + REMINDER_WINDOW else None


class MajorStageNotices:
    def __init__(self, plugin, images):
        self.plugin, self.images = plugin, images
        self.lock = asyncio.Lock()

    async def send(self, snapshot, token):
        """Retry only undelivered groups, never using a failed/stale refresh."""
        async with self.lock:
            if not token or token != self.plugin._token or not snapshot.get('serie_id'):
                return
            for stage in snapshot.get('stages') or []:
                start = due_start(stage, now_utc())
                if start is None:
                    continue
                key = f"major-stage:{snapshot['serie_id']}:{stage['id']}"
                card_key, chart_key = f'{key}:card', f'{key}:chart'
                targets = [('group', gid) for gid in groups_for(self.plugin.store, 'major_stage')]
                delivered = self.plugin.store.get_delivery_targets(key)
                pending = [(kind, gid) for kind, gid in targets if f'{kind}:{gid}' not in delivered]
                if not pending:
                    continue

                def active(target):
                    kind, gid = target
                    return (token == self.plugin._token and kind == 'group'
                            and gid in groups_for(self.plugin.store, 'major_stage')
                            and due_start(stage, now_utc()) is not None)

                # Stage reminders always attach both images (notice card + stage chart)
                # as separate image-only messages, even when routine notices use text-only mode.
                data = await self.images.notice_image(snapshot, stage, token)
                if not any(active(target) for target in pending):
                    continue
                _, _, Image = self.plugin._message_component_types()
                text = (f"【CS Major 阶段开赛提醒】\n{snapshot['title']}\n"
                        f"{stage['title']} · 北京时间 {start.astimezone(CST):%m-%d %H:%M} 开始")
                path = await asyncio.to_thread(
                    self.plugin._banner_renderer.render_notification,
                    text, self.plugin._new_notice_path(), major=stage_context(snapshot, stage, start),
                )
                card = await asyncio.to_thread(Path(path).read_bytes)
                if not any(active(target) for target in pending):
                    continue

                async def deliver_image(payload):
                    if not any(active(target) for target in pending):
                        return []
                    return await self.plugin._push_components_to_targets_with_refs(
                        pending, [Image.fromBytes(payload)], is_active=active,
                    )

                await self.plugin._deliver_notification(card_key, lambda: deliver_image(card))
                await self.plugin._deliver_notification(chart_key, lambda: deliver_image(data))
                card_done = self.plugin.store.get_delivery_targets(card_key)
                chart_done = self.plugin.store.get_delivery_targets(chart_key)
                for kind, gid in pending:
                    target_key = f'{kind}:{gid}'
                    if target_key in card_done and target_key in chart_done:
                        self.plugin.store.mark_delivery_target(key, target_key)
