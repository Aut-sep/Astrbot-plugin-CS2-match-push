"""Targeted offline LLM tool checks: fake provider, no host or QQ operations."""
from copy import deepcopy
import asyncio
import importlib
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_group_rules as fixtures
from test_start_notifications import main

NOW, row, A, B = fixtures.NOW, fixtures.row, fixtures.A, fixtures.B

ai = importlib.import_module('cs_start_checked.ai_queries')


class AIQueryTests(unittest.IsolatedAsyncioTestCase):
    # Reuse fixture only; do not rerun the unrelated group-rule suite.
    def setUp(self):
        fixtures.GroupRuleTests.setUp(self)
        self.event = types.SimpleNamespace(get_group_id=lambda: A,
            get_platform_name=lambda: 'aiocqhttp', get_platform_id=lambda: 'offline')
        self.p.client = types.SimpleNamespace(
            get_matches_between=AsyncMock(return_value=[row()]),
            get_match_result=AsyncMock(return_value=row()))
        self.clock_patch = patch.object(ai, 'now_utc', return_value=NOW)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    async def call(self, action, **params):
        return json.loads(await ai.query(self.p, self.event, action, **params))

    async def test_schedule_calendar_window_and_alias(self):
        match = row(team=124523)
        match['opponents'][0]['opponent']['name'] = 'Spirit'
        self.p.client.get_matches_between.return_value = [match]
        result = await self.call('schedule', team='绿龙', start_date='2026-11-03', days=1)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['data']['total'], 1)
        self.assertEqual(result['timezone'], 'Asia/Shanghai')
        self.assertFalse(result['is_cached'])
        self.assertIsNone(result['data_updated_at'])
        self.assertIsNotNone(result['fetched_at'])
        start, end = self.p.client.get_matches_between.call_args.args
        self.assertEqual(start.isoformat(), '2026-11-03T00:00:00+08:00')
        self.assertEqual((end-start).days, 1)

    async def test_failure_and_empty_are_different(self):
        self.p.client.get_matches_between.return_value = None
        failed = await self.call('schedule')
        self.assertEqual(failed['status'], 'unavailable')
        self.assertIsNone(failed['data'])
        self.assertIsNone(failed['fetched_at'])
        self.p.client.get_matches_between.return_value = []
        empty = await self.call('schedule')
        self.assertEqual(empty['status'], 'ok')
        self.assertEqual(empty['data']['matches'], [])

    async def test_rules_do_not_leak_other_group_following(self):
        self.p.store.set('group_rules', {A: {'followed_teams': [{'id': 1, 'name': 'NAVI'}]},
                                       B: {'followed_teams': [{'id': 3, 'name': 'Spirit'}]}})
        result = await self.call('followed')
        self.assertEqual(result['data']['teams'][0]['name'], 'NAVI')
        self.assertNotIn('Spirit', json.dumps(result))
        self.p.store.set('group_rules', {A: {'blacklist_teams': ['NAVI']}})
        self.assertEqual((await self.call('schedule'))['data']['total'], 0)

    async def test_invalid_parameters_do_not_request_provider(self):
        for params in ({'days': True}, {'days': 31}, {'start_date': '昨天'}, {'team': []}):
            self.assertEqual((await self.call('schedule', **params))['status'], 'invalid_request')
        self.p.client.get_matches_between.assert_not_awaited()

    async def test_missing_token_and_official_denial(self):
        self.p._token = ''
        self.assertEqual((await self.call('schedule'))['status'], 'not_configured')
        self.assertEqual((await self.call('followed'))['status'], 'ok')
        self.event.get_platform_name = lambda: 'qq_official'
        self.assertEqual((await self.call('followed'))['status'], 'denied')
        self.p.client.get_matches_between.assert_not_awaited()

    async def test_results_preserve_unfinished_and_unknown_scores(self):
        result = await self.call('result', match_id='1')
        self.assertEqual(result['data']['matches'][0]['status'], 'not_started')
        self.assertIsNone(result['data']['matches'][0]['final_score'])
        match = row()
        match.update(status='finished', modified_at='2026-11-03T00:00:00Z')
        self.p.client.get_matches_between.return_value = [match, row(2)]
        result = await self.call('result', start_date='2026-11-02')
        self.assertEqual(result['data']['total'], 1)
        self.assertIsNone(result['data']['matches'][0]['final_score'])
        self.assertEqual(result['data']['matches'][0]['data_updated_at'], '2026-11-03T08:00:00+08:00')

    async def test_query_never_writes_store_or_schedules(self):
        before = deepcopy(self.p.store._data)
        jobs = deepcopy(self.p._match_tasks)
        for action in ('schedule', 'result', 'followed'):
            await self.call(action)
        self.assertEqual(self.p.store._data, before)
        self.assertEqual(self.p._match_tasks, jobs)

    async def test_limit_and_sanitized_errors(self):
        self.p.client.get_matches_between.return_value = [row(mid=i) for i in range(101)]
        result = await self.call('schedule')
        self.assertTrue(result['data']['truncated'])
        self.assertEqual(len(result['data']['matches']), 100)
        self.p.client.get_matches_between.side_effect = RuntimeError('secret-token')
        self.assertNotIn('secret-token', json.dumps(await self.call('schedule')))

    async def test_major_uses_fresh_service_and_marks_next_stage(self):
        snapshot = {'title': 'Offline Major', 'serie_id': 1, 'stages': []}
        stage = {'id': '1', 'title': 'Next', 'kind': 'swiss', 'matches': [row()], 'edges': [], 'notes': ['不推断后续对手']}
        with patch.object(ai, 'MajorBracketService') as service, patch.object(ai, 'selected_stages', side_effect=[[], [stage]]):
            service.return_value.fetch = AsyncMock(return_value=snapshot)
            result = await self.call('major', stage='current')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['data']['selected_stage'], 'next')
        self.assertFalse(result['is_cached'])
        with patch.object(ai, 'MajorBracketService') as service:
            service.return_value.fetch = AsyncMock(return_value=None)
            self.assertEqual((await self.call('major'))['status'], 'unavailable')

    async def test_registered_entrypoints_return_strings(self):
        calls = [self.p.ai_cs2_schedule(self.event), self.p.ai_cs2_result(self.event),
                 self.p.ai_cs2_followed(self.event)]
        for call in calls:
            self.assertEqual(json.loads(await call)['status'], 'ok')

    async def test_confirmed_score_is_aligned_with_opponents(self):
        match = row()
        match.update(status='finished', results=[{'team_id': 2, 'score': 1}, {'team_id': 1, 'score': 2}],
                     winner={'id': 1})
        self.p.client.get_matches_between.return_value = [match]
        result = (await self.call('result'))['data']['matches'][0]
        self.assertEqual(result['final_score'], [2, 1])
        self.assertEqual(result['winner_id'], 1)

    async def test_timeout_does_not_fall_back_to_background_cache(self):
        self.p.client.get_matches_between.side_effect = asyncio.TimeoutError()
        result = await self.call('schedule')
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['data'])
        self.assertIsNone(result['fetched_at'])


