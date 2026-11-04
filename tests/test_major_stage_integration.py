"""Offline checks for Major notices integrated with per-group rules."""
import importlib
import base64
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_group_rules as fixture

NOW, A, B, row, main = fixture.NOW, fixture.A, fixture.B, fixture.row, fixture.main

major = importlib.import_module('cs_start_checked.major_stage_notices')


class MajorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.GroupRuleTests.setUp

    def snapshot(self):
        return {'serie_id': 900, 'title': 'Offline Major', 'stages': [
            {'id': '901', 'key': 'stage1', 'title': 'Stage 1',
             'status': 'not_started', 'matches': [row(minutes=60)]}]}

    def notifier(self, render=None):
        class Image:
            fromBytes = staticmethod(lambda data: data)
        self.p._message_component_types = lambda: (object, str, Image)
        self.p._banner_renderer = main.BannerRenderer()
        self.p._banner_dir = self.temp.name
        images = types.SimpleNamespace(notice_image=AsyncMock(
            side_effect=render, return_value=b'offline-image'))
        return major.MajorStageNotices(self.p, images)

    async def test_paused_group_is_excluded_even_with_global_text_mode(self):
        self.p.store.set('image_push_enabled', False)
        self.p.store.set('group_rules', {B: {'push_enabled': False}})
        notifier = self.notifier()
        with patch.object(major, 'now_utc', return_value=NOW):
            await notifier.send(self.snapshot(), self.p._token)
        self.assertEqual([item[0] for item in self.sent], [A])
        self.assertEqual(self.sent[0][2][-1], b'offline-image')
        self.assertEqual(len(self.sent[0][2]), 3)
        self.assertTrue(self.sent[0][2][1].startswith(b'\x89PNG'))
        self.assertEqual(self.p.store.get_delivery_targets('major-stage:900:901'), {f'group:{A}'})

    async def test_partial_failure_retries_only_failed_group_after_reload(self):
        async def send(context, kind, target, chain, **kwargs):
            if target == B:
                raise RuntimeError('offline transport failure')
            return await self.sender(context, kind, target, chain, **kwargs)
        notifier = self.notifier()
        with patch.object(major, 'now_utc', return_value=NOW):
            with patch.object(main, 'send_target', side_effect=send):
                await notifier.send(self.snapshot(), self.p._token)
            self.p.store = main.DataStore(self.p.store.path)
            await notifier.send(self.snapshot(), self.p._token)
            await notifier.send(self.snapshot(), self.p._token)
        self.assertEqual([item[0] for item in self.sent], [A, B])
        self.assertEqual(self.p.store.get_delivery_targets('major-stage:900:901'), {f'group:{A}', f'group:{B}'})

    async def test_pause_during_render_prevents_delivery(self):
        self.p.store.set('group_rules', {B: {'push_enabled': False}})
        async def render(*args):
            self.p.store.set('group_rules', {A: {'push_enabled': False}, B: {'push_enabled': False}})
            return b'offline-image'
        with patch.object(major, 'now_utc', return_value=NOW):
            await self.notifier(render).send(self.snapshot(), self.p._token)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.p.store.get_delivery_targets('major-stage:900:901'), set())

    async def test_outside_window_started_unknown_and_stale_token_do_not_send(self):
        notifier = self.notifier()
        with patch.object(major, 'now_utc', return_value=NOW):
            for minutes in (-1, 0, 121):
                snapshot = self.snapshot()
                snapshot['stages'][0]['matches'] = [row(minutes=minutes)]
                await notifier.send(snapshot, self.p._token)
            for status in ('running', 'finished'):
                snapshot = self.snapshot()
                snapshot['stages'][0]['matches'][0]['status'] = status
                await notifier.send(snapshot, self.p._token)
            snapshot = self.snapshot()
            snapshot['stages'][0]['matches'][0]['opponents'] = [{'opponent': {'name': 'TBD'}}]
            await notifier.send(snapshot, self.p._token)
            await notifier.send(self.snapshot(), 'outdated-token')
        self.assertEqual(self.sent, [])
        notifier.images.notice_image.assert_not_awaited()

    async def test_default_query_shows_published_next_stage_as_upcoming(self):
        snapshot = self.snapshot()
        stage = snapshot['stages'][0]
        stage.update(columns=[], notes=[], edges=[])
        self.p.bracket_images = types.SimpleNamespace(get=AsyncMock(side_effect=[
            (snapshot, [], None), (snapshot, [stage], b'chart')]))
        self.p._official_denial = lambda *args: None
        self.p.query_rules = lambda event: fixture.Rules(self.p.store)
        self.p._query_reply = AsyncMock()
        self.notifier()
        await self.p.cmd_cs_bracket(object())
        self.assertEqual([call.args[0] for call in self.p.bracket_images.get.await_args_list], ['current', 'next'])
        reply = self.p._query_reply.await_args
        self.assertIn('尚未开赛', reply.args[1])
        self.assertEqual(reply.kwargs['components'], [b'chart'])

    async def test_live_query_does_not_fall_back_to_future_stage(self):
        snapshot = self.snapshot()
        stage = snapshot['stages'][0]
        stage.update(columns=[], notes=[], edges=[])
        self.p.bracket_images = types.SimpleNamespace(get=AsyncMock(return_value=(snapshot, [stage], b'chart')))
        self.p._official_denial = lambda *args: None
        self.p.query_rules = lambda event: fixture.Rules(self.p.store)
        self.p._query_reply = AsyncMock()
        self.notifier()
        await self.p.cmd_cs_bracket(object())
        self.p.bracket_images.get.assert_awaited_once()
        self.assertNotIn('尚未开赛', self.p._query_reply.await_args.args[1])

    async def test_panel_next_stage_respects_selected_groups_text_mode(self):
        snapshot = self.snapshot()
        stage = snapshot['stages'][0]
        stage.update(columns=[], notes=[], edges=[])
        self.p.store.set('group_rules', {B: {'image_push_enabled': False}})
        self.p.bracket_images = types.SimpleNamespace(get=AsyncMock(return_value=(snapshot, [stage], None)))
        preview = await fixture.previews.build_preview(self.p, {'kind': 'bracket', 'stage': 'next', 'group_id': B})
        self.p.bracket_images.get.assert_awaited_once_with('next', images_enabled=False)
        self.assertIn('尚未开赛', preview['text'])
        self.assertIsNone(preview['image'])
        self.assertEqual(self.sent, [])

    async def test_panel_current_stage_falls_back_to_published_next_stage(self):
        snapshot = self.snapshot()
        stage = snapshot['stages'][0]
        stage.update(columns=[], notes=[], edges=[])
        self.p.bracket_images = types.SimpleNamespace(get=AsyncMock(side_effect=[
            (snapshot, [], None), (snapshot, [stage], b'offline-chart')]))
        preview = await fixture.previews.build_preview(self.p, {'kind': 'bracket', 'stage': 'current'})
        self.assertEqual([call.args[0] for call in self.p.bracket_images.get.await_args_list], ['current', 'next'])
        self.assertIn('当前无进行中阶段', preview['text'])
        self.assertIn('尚未开赛', preview['text'])
        self.assertEqual(base64.b64decode(preview['image']), b'offline-chart')

    async def test_panel_live_stage_does_not_fall_back(self):
        snapshot = self.snapshot()
        stage = snapshot['stages'][0]
        stage.update(columns=[], notes=[], edges=[])
        self.p.bracket_images = types.SimpleNamespace(get=AsyncMock(return_value=(snapshot, [stage], None)))
        preview = await fixture.previews.build_preview(self.p, {'kind': 'bracket', 'stage': 'current'})
        self.p.bracket_images.get.assert_awaited_once_with('current', images_enabled=True)
        self.assertNotIn('尚未开赛', preview['text'])

    def test_next_stage_selection_excludes_finished_and_unpublished(self):
        module = importlib.import_module('cs_start_checked.bracket_stage')
        stage = self.snapshot()['stages'][0]
        completed = {**stage, 'id': 'old', 'matches': [{**row(minutes=-60), 'status': 'finished'}]}
        unpublished = {**stage, 'id': 'unknown', 'matches': [{**row(minutes=30), 'opponents': []}]}
        self.assertEqual(module.upcoming_stages([completed, unpublished, stage], NOW), [stage])
