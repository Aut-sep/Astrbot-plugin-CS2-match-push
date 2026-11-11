"""Real persistence/scheduler/HTTP checks with offline data and mock transports."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_start_notifications import main

diagnostics_module = importlib.import_module('cs_start_checked.push_diagnostics')
web_module = importlib.import_module('cs_start_checked.web_panel')
client_module = importlib.import_module('cs_start_checked.client')
view_module = importlib.import_module('cs_start_checked.plugin_view')
NOW = datetime(2026, 11, 11, 4, 16, tzinfo=timezone.utc)
MID = 1762451
GROUPS = ['100001', '100002']


def match(**updates):
    result = {'id': MID, 'status': 'not_started', 'scheduled_at': '2026-11-11T04:30:00Z',
              'opponents': [{'opponent': {'id': 3455, 'name': 'Vitality'}},
                            {'opponent': {'id': 130564, 'name': 'Team Falcons'}}],
              'tournament': {'id': 111, 'name': 'Test', 'tier': 's'}, 'league': {'name': 'BLAST'}}
    result.update(updates)
    return result


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.now = NOW.timestamp()
        self.errors = []
        self.diag = diagnostics_module.PushDiagnostics(self.directory / 'diag.sqlite3', clock=lambda: self.now,
                                                       secrets=['private-token-value'], on_error=self.errors.append)
        self.addCleanup(self.diag.close)
        self.p = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        p = self.p
        p.store = main.DataStore(str(self.directory / 'state.json'))
        p.store.set('push_groups', GROUPS)
        p.store.set('remind_minutes', 2)
        p.store.set('min_tiers', ['s'])
        p.store.set('followed_teams', [{'id': 3455, 'name': 'Vitality'}])
        p.diagnostics = self.diag
        p._match_tasks = {}; p._result_tasks = {}; p._result_meta = {}
        p._scheduled_mids = set(); p._pending_reschedules = {}; p._background_tasks = set()
        p._schedule_daily_image_warm = lambda: None
        p._token = 'offline'; p._fetch_ahead = 3
        p.panel = types.SimpleNamespace(push_log=lambda *args: None)
        p.context = object()
        p._build_message_chain = lambda components: components
        p.client = types.SimpleNamespace(get_upcoming_matches=AsyncMock(return_value=[]),
                                          get_match_result=AsyncMock(return_value=None))

    def open_issues(self):
        return [i for i in self.diag.query()['issues'] if i['resolved_at'] is None]

    async def test_persistence_redaction_and_match_filter(self):
        self.diag.record('send_failed', key=f'upcoming:{MID}', group_id=GROUPS[0],
                         evidence={'headers': {'Authorization': 'Bearer private-token-value'},
                                   'text': 'token=private-token-value password=unlisted-secret',
                                   'url': 'https://example.invalid/?token=other-secret&x=1'},
                         error=RuntimeError('private-token-value'))
        self.diag.close()
        reloaded = diagnostics_module.PushDiagnostics(self.directory / 'diag.sqlite3', clock=lambda: self.now)
        self.addCleanup(reloaded.close)
        result = reloaded.query(match_id=MID)
        serialized = json.dumps(result)
        self.assertNotIn('private-token-value', serialized)
        self.assertNotIn('unlisted-secret', serialized)
        self.assertNotIn('other-secret', serialized)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['group_id'], GROUPS[0])

    async def test_actual_cancel_records_exact_cause_before_deleting_snapshot(self):
        planned = match()
        p = self.p
        p.store.set_match_snapshot(MID, planned['scheduled_at'], 'Vitality', 'Team Falcons')
        p.store.set_match_schedule(MID, planned['scheduled_at'])
        self.diag.track(p, planned)
        task = asyncio.create_task(asyncio.sleep(999))
        p._match_tasks[MID] = task; p._scheduled_mids.add(MID)
        p.client.get_match_result.return_value = match(scheduled_at='2026-11-11T04:15:00Z')
        with patch.object(main, 'now_utc', return_value=NOW):
            await p._fetch_and_schedule_locked()
        await asyncio.gather(task, return_exceptions=True)
        self.assertIsNone(p.store.get_match_snapshot(MID))
        event = next(e for e in self.diag.query(match_id=MID)['events']
                     if e['reason'] == 'not_started_time_elapsed')
        self.assertEqual(event['evidence']['old_snapshot']['sched'], planned['scheduled_at'])
        self.assertEqual(event['evidence']['match']['scheduled_at'], '2026-11-11T04:15:00Z')
        self.assertTrue(event['evidence']['selection']['groups'][GROUPS[0]]['accepted'])
        self.assertFalse(event['evidence']['result_handoff'])
        self.now = datetime(2026, 11, 11, 4, 33, tzinfo=timezone.utc).timestamp()
        state_before = Path(p.store.path).read_bytes()
        self.diag.audit(p)
        self.assertEqual({i['reason'] for i in self.open_issues()}, {'missing_upcoming', 'result_tracking_lost'})
        event_count = self.diag.query()['event_count']
        self.diag.audit(p)
        self.assertEqual(self.diag.query()['event_count'], event_count)
        self.assertEqual(Path(p.store.path).read_bytes(), state_before)
        p.client.get_match_result.assert_awaited_once()

    async def test_cancel_reasons_have_distinct_codes(self):
        cases = [(match(scheduled_at=None), True, 'not_started_time_invalid'),
                 (match(scheduled_at='bad'), True, 'not_started_time_invalid'),
                 (match(), False, 'selection_rejected'),
                 (match(status='canceled'), True, 'status_canceled'),
                 (match(status='postponed'), True, 'status_postponed'),
                 (match(status='running'), True, 'handoff_running'),
                 (match(status='finished'), True, 'handoff_finished'),
                 (match(scheduled_at='2026-11-20T04:30:00Z'), True, 'outside_window')]
        for row, accepted, expected in cases:
            self.assertEqual(diagnostics_module.cleanup_reason(row, accepted, NOW, NOW + timedelta(days=3)), expected)

    async def test_partial_delivery_policy_and_recovery_without_state_writes(self):
        self.diag.track(self.p, match())
        self.now += 20 * 60
        self.p.store.mark_delivery_target(f'upcoming:{MID}', 'group:' + GROUPS[0])
        self.p._result_tasks[MID] = types.SimpleNamespace(done=lambda: False)
        self.diag.audit(self.p)
        self.assertEqual([(i['group_id'], i['reason']) for i in self.open_issues()], [(GROUPS[1], 'missing_upcoming')])
        self.p.store.mark_delivery_target(f'upcoming:{MID}', 'group:' + GROUPS[1])
        self.diag.audit(self.p)
        self.assertFalse(self.open_issues())
        self.assertTrue(any(e['reason'] == 'issue_resolved' for e in self.diag.query()['events']))

    async def test_disabled_historical_and_canceled_matches_are_not_misses(self):
        self.p.store.set('group_rule_since', {gid: (NOW + timedelta(hours=1)).isoformat() for gid in GROUPS})
        self.diag.track(self.p, match())
        self.now += 20 * 60
        self.p.store.set('finished_enabled', False)
        self.diag.audit(self.p)
        self.assertFalse(self.open_issues())
        self.p.store.set('finished_enabled', True)
        self.diag.record('single_match_received', match=match(status='canceled'))
        self.diag.audit(self.p)
        self.assertFalse(self.open_issues())

    async def test_result_finished_missing_and_policy_change_resolves(self):
        self.diag.track(self.p, match(), phase='result')
        self.diag.record('single_match_received', match=match(status='finished', end_at='2026-11-11T04:40:00Z'))
        self.now += 30 * 60
        self.diag.audit(self.p)
        self.assertEqual({i['reason'] for i in self.open_issues()}, {'missing_finished'})
        self.p.store.set('push_enabled', False)
        self.diag.audit(self.p)
        self.assertFalse(self.open_issues())

    async def test_send_error_and_receipt_error_are_recorded_per_group(self):
        async def send():
            return await self.p._push_components_to_targets_with_refs([('group', GROUPS[0])], ['offline'])
        with patch.object(main, 'send_target', new=AsyncMock(side_effect=RuntimeError('token=private-token-value'))):
            self.assertFalse(await self.p._deliver_notification(f'upcoming:{MID}', send))
        events = self.diag.query(match_id=MID)['events']
        self.assertEqual({e['reason'] for e in events}, {'send_attempt', 'send_failed'})
        self.assertNotIn('private-token-value', json.dumps(events))
        with patch.object(main, 'send_target', new=AsyncMock(return_value={'message_id': 'accepted'})), \
                patch.object(self.p.store, 'mark_delivery_target', side_effect=OSError('disk full')):
            self.assertFalse(await self.p._deliver_notification(f'upcoming:{MID}', send))
        failure = next(e for e in self.diag.query(match_id=MID)['events'] if e['reason'] == 'receipt_failed')
        self.assertTrue(failure['evidence']['platform_accepted'])
        self.assertFalse(self.diag._delivered(f'upcoming:{MID}', GROUPS[0]))

    async def test_diagnostic_storage_failure_does_not_stop_delivery(self):
        self.diag.db.execute('PRAGMA query_only=ON')
        with patch.object(main, 'send_target', new=AsyncMock(return_value={'message_id': 'ok'})) as transport:
            result = await self.p._deliver_notification(f'upcoming:{MID}', lambda:
                self.p._push_components_to_targets_with_refs([('group', GROUPS[0])], ['offline']))
        self.assertTrue(result)
        transport.assert_awaited_once()
        self.assertFalse(self.diag.query()['health']['available'])
        self.assertTrue(self.errors)

    async def test_api_observation_and_failure_are_correlated_to_requested_match(self):
        client = client_module.PandaScoreClient('offline')
        client.diagnostics = self.diag
        client._request = AsyncMock(return_value=match())
        await client.get_match_result(MID)
        event = self.diag.query(match_id=MID)['events'][0]
        self.assertEqual(event['reason'], 'single_match_received')
        self.assertEqual(event['evidence']['match']['opponents'][0]['opponent']['id'], 3455)
        client._request.return_value = match(id=999)
        await client.get_match_result(MID)
        self.assertEqual(self.diag.query(match_id=MID)['events'][0]['evidence']['match']['id'], 999)
        self.diag.record('request_failed', evidence={'endpoint': f'/matches/{MID}', 'http_status': 429})
        self.assertEqual(self.diag.query(match_id=MID)['events'][0]['reason'], 'request_failed')

    async def test_diagnostic_http_requires_auth_and_validates_query(self):
        from aiohttp.test_utils import TestClient, TestServer
        self.p.store.set('web_panel_token', 'test-panel-key')
        panel = web_module.WebPanel(self.p)
        client = TestClient(TestServer(panel.app))
        await client.start_server()
        try:
            response = await client.get('/api/diagnostics')
            self.assertEqual(response.status, 401)
            headers = {'Authorization': 'Bearer test-panel-key'}
            response = await client.get('/api/diagnostics?match_id=bad', headers=headers)
            self.assertEqual(response.status, 400)
            response = await client.get('/api/diagnostics?limit=1001', headers=headers)
            self.assertEqual(response.status, 400)
            self.diag.record('send_success', key=f'finished:{MID}', group_id=GROUPS[0])
            response = await client.get(f'/api/diagnostics?match_id={MID}', headers=headers)
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertEqual(data['event_count'], 1)
            self.assertEqual(data['events'][0]['event_key'], f'finished:{MID}')
            response = await client.get('/api/diagnostics?view=bad', headers=headers)
            self.assertEqual(response.status, 400)
            response = await client.get('/api/diagnostics?view=matches', headers=headers)
            data = await response.json()
            self.assertEqual(data['matches'][0]['match_id'], MID)
            response = await client.get(f'/api/diagnostics?view=detail&match_id={MID}', headers=headers)
            data = await response.json()
            self.assertEqual(data['event_groups']['delivery'][0]['reason'], 'send_success')
            self.assertIn(('GET', r'/api/diagnostics', 'get_diagnostics'), view_module.ROUTES)
        finally:
            await client.close()

    async def test_new_schedule_does_not_alert_on_old_reminder_time(self):
        self.diag.track(self.p, match())
        self.now += 20 * 60
        self.diag.track(self.p, match(scheduled_at='2026-11-11T05:30:00Z'))
        self.diag.audit(self.p)
        self.assertFalse(self.open_issues())

    async def test_daily_pending_and_bounded_retention(self):
        self.p.store.set('daily_push_enabled', True)
        self.p.store.set('daily_push_events', {'daily:100001:2026-11-11_12:00': 'pending'})
        self.diag.audit(self.p)
        self.assertEqual({i['reason'] for i in self.open_issues()}, {'daily_overdue'})
        with patch.object(diagnostics_module, 'MAX_EVENTS', 3):
            for i in range(8):
                self.diag.record('schedule_received', match_id=i)
            self.diag._prune(self.now)
            self.assertLessEqual(self.diag.query()['event_count'], 3)
        self.now += 31 * 86400
        self.diag._prune(self.now)
        self.assertFalse(self.diag.query()['events'])

    async def test_malformed_response_fields_remain_available_for_diagnosis(self):
        broken = match(opponents='unexpected-list-type', tournament=42)
        self.diag.record('single_match_received', match=broken)
        event = self.diag.query(match_id=MID)['events'][0]
        self.assertEqual(event['evidence']['match']['opponents']['invalid_field_type'], 'str')
        self.assertEqual(event['evidence']['match']['tournament']['value'], 42)
        self.assertIn('invalid_selection_data', diagnostics_module.selection_evidence(self.p.store, broken))

    async def test_recorded_success_survives_receipt_pruning(self):
        self.diag.track(self.p, match())
        for gid in GROUPS:
            self.diag.record('send_success', key=f'upcoming:{MID}', group_id=gid)
            self.diag.record('send_success', key=f'finished:{MID}', group_id=gid)
        self.now += 24 * 3600
        self.diag.audit(self.p)
        self.assertFalse(self.open_issues())

    async def test_manual_reset_preserves_evidence_and_resets_old_expectations(self):
        self.diag.track(self.p, match())
        self.now += 20 * 60
        self.diag.audit(self.p)
        self.assertTrue(self.open_issues())
        self.p.clear_match_notifications()
        self.diag.audit(self.p)
        self.assertFalse(self.open_issues())
        self.assertTrue(any(e['reason'] == 'notification_reset' for e in self.diag.query()['events']))
        self.assertTrue(any(e['reason'] == 'missing_upcoming' for e in self.diag.query()['events']))

    async def test_late_observation_does_not_overwrite_original_reminder_plan(self):
        self.diag.track(self.p, match())
        self.diag.track(self.p, match(scheduled_at='2026-11-11T04:15:00Z'), phase='observed')
        row = self.diag.db.execute('SELECT planned FROM watches WHERE match_id=?', (MID,)).fetchone()
        self.assertEqual(json.loads(row['planned'])['scheduled_at'], '2026-11-11T04:30:00Z')

    async def test_embedded_export_validates_id(self):
        view = view_module.PluginView(self.p)
        self.p.panel = web_module.WebPanel(self.p)
        view._json_response = lambda payload, **kwargs: {'payload': payload, **kwargs}
        view._request = types.SimpleNamespace(query={'match_id': 'bad'})
        self.assertEqual((await view.export_diagnostics())['status_code'], 400)
        view._request = types.SimpleNamespace(query={'match_id': str(MID)})
        result = await view.export_diagnostics()
        self.assertEqual(result['status_code'], 200)
        self.assertIn('attachment', result['headers']['Content-Disposition'])


if __name__ == '__main__':
    unittest.main()
