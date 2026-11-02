"""Targeted offline checks for filtering, batching and per-group retries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_start_notifications import main

notices = importlib.import_module('cs_start_checked.reschedule_notices')
REAL_COMPONENTS = notices.components
NOW = datetime(2026, 11, 2, 12, 21, tzinfo=timezone.utc)


def match(mid=1, hours=1, tbd=False):
    return {
        'id': mid, 'status': 'not_started', 'scheduled_at': (NOW + timedelta(hours=hours)).isoformat(),
        'league': {'name': 'IEM'}, 'tournament': {'tier': 's'},
        'opponents': [{'opponent': {'id': 1, 'name': 'Spirit'}},
                      {'opponent': {'id': 2, 'name': 'TBD' if tbd else 'Natus Vincere'}}],
    }


class RescheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plugin = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        p = self.plugin
        p.store = main.DataStore(str(Path(self.temp.name) / 'data.json'))
        p.store.set('push_groups', ['100001'])
        p._pending_reschedules = {}
        p.panel = types.SimpleNamespace(push_log=lambda *args: None)
        p.context = object()
        p._build_message_chain = lambda items: items
        p._format = lambda function, *args: function(*args)
        self.sends, self.renders = [], []

        async def send(context, kind, target, chain, **kwargs):
            self.sends.append((target, tuple(chain)))
            return {'message_id': len(self.sends)}

        async def render(plugin, batch):
            ids = tuple(change.match['id'] for change in batch)
            self.renders.append(ids)
            return list(ids)

        self.sender = send
        for patcher in (patch.object(main, 'send_target', side_effect=send),
                        patch.object(notices, 'components', side_effect=render),
                        patch.object(main, 'now_utc', return_value=NOW)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_filter_boundaries_and_confirmed_teams(self):
        old = lambda hours: (NOW + timedelta(hours=hours)).isoformat()
        for old_hours, new_hours, reminded, expected in (
            (1, 4, False, True), (4, 1, False, True), (4, 5, False, False),
            (4, 5, True, True), (2, 5, False, True), (2.01, 5, False, False),
        ):
            with self.subTest(old=old_hours, new=new_hours, reminded=reminded):
                self.assertEqual(notices.eligible(match(hours=new_hours), old(old_hours), NOW, reminded), expected)
        self.assertFalse(notices.eligible(match(tbd=True), old(0.5), NOW, True))
        self.assertFalse(notices.eligible({**match(), 'status': 'running'}, old(0.5), NOW))
        self.assertFalse(notices.eligible(match(hours=0), old(0.5), NOW))
        self.assertFalse(notices.eligible(match(), 'invalid', NOW))
        self.assertFalse(notices.schedule_changed('2026-11-02T13:00:00Z', '2026-11-02T21:00:00+08:00'))
        self.assertFalse(notices.eligible(match(), (NOW + timedelta(hours=1, seconds=15)).isoformat(), NOW))

    async def test_three_match_threshold_and_no_repeat(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                self.sends.clear(); self.renders.clear()
                matches = [match(mid) for mid in range(1, count + 1)]
                for row in matches:
                    notices.queue_change(self.plugin, row, (NOW + timedelta(minutes=30)).isoformat(), NOW)
                await notices.send_pending(self.plugin, matches, NOW)
                self.assertEqual(len(self.sends), 1 if count == 3 else count)
                self.assertEqual([len(x) for x in self.renders], [3] if count == 3 else [1] * count)
                self.assertFalse(self.plugin._pending_reschedules)
                self.assertFalse(self.plugin.store.get('delivery_receipts'))
                await notices.send_pending(self.plugin, matches, NOW)
                self.assertEqual(len(self.sends), 1 if count == 3 else count)

    async def test_text_mode_uses_the_same_batch_rules(self):
        p = self.plugin
        p.store.set('image_push_enabled', False)
        p._notice_teams = lambda **kwargs: []
        p._resolve_team_marks = AsyncMock(return_value=[])
        p._team_text_components = AsyncMock(return_value=['text summary'])
        rows = [match(mid) for mid in range(1, 4)]
        for row in rows:
            notices.queue_change(p, row, (NOW + timedelta(minutes=30)).isoformat(), NOW)
        changes = list(p._pending_reschedules.values())
        result = await REAL_COMPONENTS(p, changes)
        self.assertEqual(result, ['text summary'])
        self.assertIn('共 3 场', p._team_text_components.call_args.args[0])
        self.assertEqual(p._team_text_components.call_args.args[0].count('Spirit vs NAVI'), 3)
        p._notification_components = AsyncMock(return_value=['single notice'])
        self.assertEqual(await REAL_COMPONENTS(p, changes[:1]), ['single notice'])

    async def test_partial_group_failure_and_changed_batch(self):
        p = self.plugin
        p.store.set('push_groups', ['100001', '100002'])
        rows = [match(mid) for mid in range(1, 4)]
        for row in rows:
            notices.queue_change(p, row, (NOW + timedelta(minutes=30)).isoformat(), NOW)

        async def fail_second(context, kind, target, chain, **kwargs):
            if target == '100002':
                raise RuntimeError('offline failure')
            return await self.sender(context, kind, target, chain, **kwargs)

        with patch.object(main, 'send_target', side_effect=fail_second):
            await notices.send_pending(p, rows, NOW)
        self.assertEqual(self.sends, [('100001', (1, 2, 3))])
        self.assertEqual(len(p._pending_reschedules), 3)
        rows.append(match(4))
        notices.queue_change(p, rows[-1], (NOW + timedelta(minutes=30)).isoformat(), NOW)
        await notices.send_pending(p, rows, NOW)
        self.assertEqual(self.sends, [('100001', (1, 2, 3)), ('100001', (4,)), ('100002', (1, 2, 3, 4))])
        self.assertFalse(p._pending_reschedules)
        self.assertFalse(p.store.get('delivery_receipts'))

    def prepare_fetch(self, rows, old_hours):
        p = self.plugin
        p._token, p._fetch_ahead = 'offline-placeholder', 2
        p._scheduled_mids = {row['id'] for row in rows}
        p._match_tasks = {}
        self.cancelled, self.started = [], []
        for row in rows:
            mid = row['id']
            p.store.set_match_snapshot(mid, (NOW + timedelta(hours=old_hours)).isoformat(),
                                       'Spirit', 'TBD' if row['opponents'][1]['opponent']['name'] == 'TBD' else 'Natus Vincere')
            p._match_tasks[mid] = types.SimpleNamespace(done=lambda: False, cancel=lambda mid=mid: self.cancelled.append(mid))
        p._schedule_daily_image_warm = lambda: None

        def start(row, minutes):
            self.started.append(row['id'])
            p._scheduled_mids.add(row['id'])

        p._start_match_remind_task = start
        p.client = types.SimpleNamespace(get_upcoming_matches=AsyncMock(return_value=deepcopy(rows)))

    async def test_far_batch_updates_schedule_and_rebuilds_without_messages(self):
        rows = [match(mid, hours=14 + mid, tbd=mid == 6) for mid in range(1, 8)]
        self.prepare_fetch(rows, old_hours=23)
        await self.plugin._fetch_and_schedule_locked()
        self.assertEqual(self.sends, [])
        self.assertEqual(len(self.started), 7)
        self.assertEqual(len(self.cancelled), 7)
        self.assertFalse(self.plugin._pending_reschedules)
        self.assertEqual(self.plugin.store.get_match_snapshot(1)['sched'], rows[0]['scheduled_at'])
        await self.plugin._fetch_and_schedule_locked()
        self.assertEqual(len(self.started), 7)

    async def test_reminder_receipt_is_captured_before_rebuild_clears_it(self):
        rows = [match(hours=5)]
        self.prepare_fetch(rows, old_hours=4)
        self.plugin.store.mark_upcoming_notified(1)
        await self.plugin._fetch_and_schedule_locked()
        self.assertEqual(self.sends, [('100001', (1,))])
        self.assertEqual(self.started, [1])
        self.assertFalse(self.plugin.store.is_upcoming_notified(1))

    async def test_near_match_moved_beyond_fetch_window_still_notifies_and_retries(self):
        row = match(hours=72)
        self.prepare_fetch([row], old_hours=1)
        p = self.plugin
        p.client.get_upcoming_matches = AsyncMock(return_value=[])
        p.client.get_match_result = AsyncMock(side_effect=[deepcopy(row), None, deepcopy(row)])
        with patch.object(main, 'send_target', new=AsyncMock(side_effect=RuntimeError('offline failure'))):
            await p._fetch_and_schedule_locked()
        self.assertEqual(len(p._pending_reschedules), 1)
        self.assertEqual(self.started, [])
        await p._fetch_and_schedule_locked()
        self.assertEqual(len(p._pending_reschedules), 1)
        self.assertEqual(self.sends, [])
        await p._fetch_and_schedule_locked()
        self.assertEqual(self.sends, [('100001', (1,))])
        self.assertFalse(p._pending_reschedules)
        self.assertEqual(p.client.get_match_result.await_count, 3)

    async def test_retry_drops_tbd_and_obsolete_or_disabled_changes(self):
        p = self.plugin
        for replacement in ('tbd', 'replaced', 'removed', 'disabled'):
            with self.subTest(replacement=replacement):
                p.store.set('reschedule_notify', True)
                row = match()
                notices.queue_change(p, row, (NOW + timedelta(minutes=30)).isoformat(), NOW)
                current = [row]
                if replacement == 'tbd': current = [match(tbd=True)]
                if replacement == 'replaced': current = [match(hours=5)]
                if replacement == 'removed': current = []
                if replacement == 'disabled': p.store.set('reschedule_notify', False)
                await notices.send_pending(p, current, NOW)
                self.assertFalse(p._pending_reschedules)
        self.assertEqual(self.sends, [])


if __name__ == '__main__':
    unittest.main()
