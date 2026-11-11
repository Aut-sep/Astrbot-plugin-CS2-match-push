"""Per-group notification scheduling on top of shared match/result tracking."""
import asyncio
from copy import deepcopy
from datetime import timedelta

from astrbot.api import logger
from .formatters import CST, fmt_daily_schedule, fmt_tournament_announce, is_push_ready_match, parse_dt, sched_str, team_name
from .group_rules import Rules, groups_for, scope
from .push_diagnostics import emit as diagnostic_event


def now_utc():
    # Share the plugin clock, including deterministic offline tests.
    from .main import now_utc as clock
    return clock()


class GroupNotifications:
    def __init__(self, plugin):
        self.p = plugin

    def current_groups(self, kind, match=None, event=None):
        return groups_for(self.p.store, kind, match, event=event)

    def active(self, gid, kind=None, match=None, event=None):
        if gid not in self.current_groups(kind, match, event):
            return False
        if kind == 'finished' and match and match.get('end_at'):
            try:
                return self.not_historical(gid, parse_dt(match['end_at']))
            except (TypeError, ValueError):
                return False
        return True

    def freeze(self, key, groups):
        try:
            return self.p.store.freeze_targets(key, groups)
        except OSError as exc:
            diagnostic_event(self.p, 'notification_failed', key=key, level='ERROR', error=exc,
                             evidence={'stage': 'freeze_targets'})
            logger.exception('[CS] 无法保存通知目标，本轮不投递，稍后重试')
            return None

    def reminder_time(self, match, gid):
        custom = self.p.store.get_custom_remind(match['id'])
        minutes = custom if custom is not None else Rules(self.p.store, gid).get('remind_minutes', 10)
        return parse_dt(sched_str(match)) - timedelta(minutes=minutes), minutes

    def since(self, gid):
        value = self.p.store.get('group_rule_since', {}).get(gid)
        return parse_dt(value) if value else None

    def not_historical(self, gid, trigger):
        since = self.since(gid)
        return since is None or trigger >= since

    async def deliver(self, key, gid, kind, sender, *, match=None, event=None, extra=None):
        def gate():
            return self.active(gid, kind, match, event) and (extra is None or extra())
        if not gate():
            diagnostic_event(self.p, 'send_blocked', key=key, group_id=gid,
                             evidence={'cause': 'group_policy_or_historical'})
            return False
        async def attempt():
            if f'group:{gid}' in self.p.store.get_delivery_targets(key):
                return True
            return await sender()
        with scope(Rules(self.p.store, gid), gate=gate):
            return await self.p._deliver_notification(key, attempt)

    async def run_remind(self, match):
        p, mid = self.p, match['id']
        while now_utc() < parse_dt(sched_str(match)):
            if p.store.is_finished_notified(mid):
                return False
            key = f'upcoming:{mid}'
            frozen = p.store.get('notification_targets', {}).get(key)
            if p.store.is_upcoming_notified(mid) and frozen is None:
                # Legacy completed broadcasts have no target snapshot. Never replay them.
                await asyncio.sleep(max(0, (parse_dt(sched_str(match)) - now_utc()).total_seconds()))
                return True
            eligible = [gid for gid in self.current_groups('upcoming', match)
                        if self.not_historical(gid, self.reminder_time(match, gid)[0])]
            if frozen is not None:
                # New groups may enroll only before their own future trigger. They
                # never join an already due/failed reminder's retry audience.
                additional = [gid for gid in eligible if gid not in frozen
                              and self.reminder_time(match, gid)[0] > now_utc()]
                if additional:
                    frozen = [*frozen, *additional]
                    p.store._data['notification_targets'][key] = frozen
                    if mid in p.store._data['notified_upcoming']:
                        p.store._data['notified_upcoming'].remove(mid)
                    p.store.save()
                eligible = [gid for gid in eligible if gid in frozen]
            delivered = p.store.get_delivery_targets(key)
            waiting = [gid for gid in eligible if f'group:{gid}' not in delivered]
            now = now_utc()
            due = [gid for gid in waiting if self.reminder_time(match, gid)[0] <= now]
            if due:
                fresh = await p.client.get_match_result(mid)
                if fresh and str(fresh.get('id')) == str(mid):
                    status = fresh.get('status')
                    if status in {'canceled', 'postponed'}:
                        diagnostic_event(p, 'notification_skipped', match=fresh, key=key,
                                         evidence={'cause': status})
                        return False
                    if status in {'running', 'finished'}:
                        diagnostic_event(p, 'notification_skipped', match=fresh, key=key,
                                         evidence={'cause': 'already_started_handoff'})
                        match.clear(); match.update(deepcopy(fresh))
                        return True
                    if status == 'not_started':
                        try:
                            start = parse_dt(sched_str(fresh))
                        except (TypeError, ValueError):
                            diagnostic_event(p, 'notification_skipped', match=fresh, key=key, level='WARN',
                                             evidence={'cause': 'invalid_time'})
                            return False
                        changed = start != parse_dt(sched_str(match))
                        match.clear(); match.update(deepcopy(fresh))
                        p.store.set_match_snapshot(mid, sched_str(match), team_name(match, 0), team_name(match, 1))
                        p.store.set_match_schedule(mid, sched_str(match))
                        if changed:
                            continue
                        if start <= now_utc():
                            return True
                        if is_push_ready_match(match):
                            # Freeze before the first real attempt, not when a future task is created.
                            audience = self.freeze(key, eligible)
                            for gid in (due if audience is not None else []):
                                if gid not in audience:
                                    continue
                                rules = Rules(p.store, gid)
                                minutes = self.reminder_time(match, gid)[1]
                                await self.deliver(key, gid, 'upcoming',
                                    lambda minutes=minutes, rules=rules: p._push_upcoming(
                                        match, minutes, p.store.get('custom_streams') or [], rules.get_followed_team_names()),
                                    match=match)
                        else:
                            diagnostic_event(p, 'notification_skipped', match=match, key=key,
                                             evidence={'cause': 'teams_unconfirmed'}, dedupe=f'unconfirmed:{mid}')
                else:
                    p.panel.push_log('WARN', f'比赛 {mid} 赛前状态未核实，暂不发送提醒')
            delivered = p.store.get_delivery_targets(key)
            if eligible and all(f'group:{gid}' in delivered for gid in eligible):
                p.store.mark_upcoming_notified(mid)
                remaining = (parse_dt(sched_str(match)) - now_utc()).total_seconds()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                return True
            future = [(self.reminder_time(match, gid)[0] - now_utc()).total_seconds()
                      for gid in waiting if self.reminder_time(match, gid)[0] > now_utc()]
            remaining = (parse_dt(sched_str(match)) - now_utc()).total_seconds()
            if remaining > 0:
                await asyncio.sleep(min([60, remaining, *future]))
        return True

    async def started(self, match):
        p, key = self.p, f"upcoming:{match['id']}"
        # A newly added/re-enabled group is not backfilled a match already running.
        try:
            begin = parse_dt(match.get('begin_at') or sched_str(match))
        except (TypeError, ValueError):
            try:
                begin = parse_dt(sched_str(match))
            except (TypeError, ValueError):
                return False
        recipients = [gid for gid in self.current_groups('upcoming', match) if self.not_historical(gid, begin)]
        audience = self.freeze(key, recipients)
        if audience is None:
            return False
        active = [gid for gid in audience if gid in recipients]
        success = bool(active)
        for gid in active:
            success = await self.deliver(key, gid, 'upcoming', lambda: p._push_started(match), match=match) and success
        return success

    async def finished(self, match):
        p, key = self.p, f"finished:{match['id']}"
        audience = self.freeze(key, [gid for gid in self.current_groups('finished', match) if self.active(gid, 'finished', match)])
        if audience is None:
            return False
        success = True
        for gid in audience:
            if not self.active(gid, 'finished', match):
                continue
            rules = Rules(p.store, gid)
            success = await self.deliver(key, gid, 'finished',
                lambda rules=rules: p._push_finished(match, rules.get_followed_team_names()), match=match) and success
        return success

    def schedule_rows(self, rules):
        return [deepcopy(row) for row in self.p._scheduled if rules.accepts(row)]

    def daily_payload(self, gid, days, rows=None):
        rules = Rules(self.p.store, gid)
        rows = self.schedule_rows(rules) if rows is None else [deepcopy(row) for row in rows if rules.accepts(row)]
        rows = self.p._daily_notice_matches(rows, days)
        if not rows:
            return None
        return {'text': self.p._format(fmt_daily_schedule, rows, rules.get_followed_team_names(), days),
                'matches': rows, 'days': days, 'created_at': now_utc().isoformat(),
                'rules': deepcopy(rules.values)}

    async def manual(self, days, group_id=None):
        p = self.p
        groups = [group_id] if group_id else self.current_groups(None)
        for gid in groups:
            if not self.active(gid):
                continue
            payload = self.daily_payload(gid, days)
            if payload:
                with scope(Rules(p.store, gid), gate=lambda gid=gid: self.active(gid)):
                    await p._push(payload['text'], matches=payload['matches'], daily_days=days)

    @staticmethod
    def daily_key(gid, day, hhmm):
        return f'daily:{gid}:{day}_{hhmm}'

    def configured_daily(self, now):
        today = now.astimezone(CST).date().isoformat()
        events = {}
        for gid in self.current_groups('daily'):
            rules = Rules(self.p.store, gid)
            for hhmm in rules.get('daily_push_times', []):
                hour, minute = map(int, hhmm.split(':'))
                trigger = now.astimezone(CST).replace(hour=hour, minute=minute, second=0, microsecond=0)
                events[self.daily_key(gid, today, hhmm)] = (gid, trigger)
        return events

    def migrate_daily(self, now):
        """Split existing same-day events without replaying completed broadcasts."""
        p = self.p
        for key, status in list(p.store.get('daily_push_events', {}).items()):
            if not key.startswith('daily:') or key.count(':') != 2:
                # Legacy key: daily:YYYY-MM-DD_HH:MM (two colons).
                continue
            part = key[len('daily:'):]
            day, separator, hhmm = part.partition('_')
            if not separator or day != now.astimezone(CST).date().isoformat():
                continue
            old_payload = p.store.get_daily_push_payload(key)
            receipts = p.store.get_delivery_targets(key)
            for gid in p.store.get_groups():
                rules = Rules(p.store, gid)
                if hhmm not in rules.get('daily_push_times', []):
                    continue
                new_key = self.daily_key(gid, day, hhmm)
                if p.store.get_daily_push_status(new_key) is not None:
                    continue
                delivered = f'group:{gid}' in receipts
                if status in {'complete', 'skipped'} or delivered:
                    p.store.mark_daily_push_status(new_key, 'complete' if delivered else status)
                elif old_payload:
                    payload = self.daily_payload(gid, old_payload.get('days') or 1, old_payload['matches'])
                    p.store.mark_daily_push_status(new_key, 'pending' if payload else 'skipped', payload)
                else:
                    p.store.mark_daily_push_status(new_key, 'pending')

    async def daily_tick(self, now, watermark):
        p = self.p
        events = self.configured_daily(now)
        for key, (gid, trigger) in events.items():
            if watermark < trigger <= now and self.not_historical(gid, trigger):
                if p.store.get_daily_push_status(key) is None:
                    p.store.mark_daily_push_status(key, 'pending')
        p.store.prune_daily_push_events(set(events))
        for key, (gid, trigger) in sorted(events.items()):
            if p.store.get_daily_push_status(key) != 'pending':
                continue
            current = self.configured_daily(now_utc())
            if key not in current:
                continue
            payload = p.store.get_daily_push_payload(key)
            if payload is None:
                if not p._schedule_data_ready:
                    continue
                payload = self.daily_payload(gid, Rules(p.store, gid).get('daily_push_days', 1))
                if payload is None:
                    p.store.mark_daily_push_status(key, 'skipped')
                    continue
                p.store.mark_daily_push_status(key, 'pending', payload)
            # Settings changed since an attempt: discard the old report, rather than send stale content.
            if not self.not_historical(gid, parse_dt(payload.get('created_at') or trigger.isoformat())):
                p.store.mark_daily_push_status(key, 'skipped')
                continue
            def gate():
                return key in self.configured_daily(now_utc()) and self.not_historical(
                    gid, parse_dt(payload.get('created_at') or trigger.isoformat()))
            # Keep the frozen report's display mode and contents across retries.
            with scope(Rules(p.store, gid, values=payload.get('rules')), gate=gate):
                sent = await p._deliver_notification(key, lambda: p._push(
                    payload['text'], matches=payload['matches'], daily_days=payload.get('days') or 1))
            if sent and gate():
                p.store.mark_daily_push_status(key, 'complete')

    async def daily_loop(self):
        start = now_utc().replace(second=0, microsecond=0)
        watermark = start - timedelta(minutes=1)
        self.migrate_daily(start)
        while True:
            try:
                await asyncio.sleep(60)
                now = now_utc()
                await self.daily_tick(now, watermark)
                watermark = max(watermark, now)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                diagnostic_event(self.p, 'task_exception', level='ERROR', error=exc, evidence={'task': 'daily_push_loop'})
                logger.exception('[CS] 分群日报处理失败，将在下一轮继续')

    async def opening(self, event):
        p, identity = self.p, event['id']
        key = f'series:{identity}'
        while now_utc() < parse_dt(event['begin_at']):
            if p.store.is_series_notified(identity):
                return
            active = self.current_groups('opening', event=event)
            def trigger(gid):
                return parse_dt(event['begin_at']) - timedelta(hours=Rules(p.store, gid).get('tournament_announce_hours', 2))
            active = [gid for gid in active if self.not_historical(gid, trigger(gid))]
            frozen = p.store.get('notification_targets', {}).get(key)
            if frozen is not None:
                active = [gid for gid in active if gid in frozen]
            receipts = p.store.get_delivery_targets(key)
            waiting = [gid for gid in active if f'group:{gid}' not in receipts]
            due = [gid for gid in waiting if trigger(gid) <= now_utc()]
            if due:
                fresh = await p.client.get_event(event)
                if fresh:
                    old_start = event['begin_at']
                    event = fresh
                    if parse_dt(event['begin_at']) <= now_utc():
                        return
                    if old_start != event['begin_at']:
                        continue
                    audience = self.freeze(key, active)
                    for gid in (due if audience is not None else []):
                        if gid not in audience:
                            continue
                        rules = Rules(p.store, gid)
                        hours = rules.get('tournament_announce_hours', 2)
                        text = p._format(fmt_tournament_announce, event, rules.get_followed_team_names(), hours,
                                         rules.follows(event.get('teams') or []))
                        await self.deliver(key, gid, 'opening', lambda text=text: p._push(
                            text, teams=event.get('teams') or [], competition=event), event=event)
            receipts = p.store.get_delivery_targets(key)
            if active and all(f'group:{gid}' in receipts for gid in active):
                p.store.mark_series_notified(identity)
                return
            remaining = (parse_dt(event['begin_at']) - now_utc()).total_seconds()
            if remaining <= 0:
                return
            future = [(trigger(gid) - now_utc()).total_seconds() for gid in waiting if trigger(gid) > now_utc()]
            await asyncio.sleep(min([60, remaining, *future]))
