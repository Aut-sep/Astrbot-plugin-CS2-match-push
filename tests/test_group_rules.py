"""Offline group-rule regressions. All remote data and QQ sends are substitutes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_start_notifications import main
rules_module = importlib.import_module('cs_start_checked.group_rules')
Rules, scope = rules_module.Rules, rules_module.scope
notices = importlib.import_module('cs_start_checked.reschedule_notices')
previews = importlib.import_module('cs_start_checked.panel_previews')
config = importlib.import_module('cs_start_checked.config')
web = importlib.import_module('cs_start_checked.web_panel')
NOW = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
A, B, C = '100001', '100002', '100003'


def row(mid=1, team=1, tier='s', minutes=60):
    return {'id': mid, 'status': 'not_started', 'scheduled_at': (NOW + timedelta(minutes=minutes)).isoformat(),
            'league': {'id': 10, 'name': 'Test League'}, 'tournament': {'id': 20, 'name': 'Test Cup', 'tier': tier},
            'opponents': [{'opponent': {'id': team, 'name': {1: 'NAVI', 3: 'Spirit'}.get(team, 'Other')}},
                          {'opponent': {'id': 2, 'name': 'G2'}}], 'number_of_games': 3}


class GroupRuleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.p = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        p = self.p
        p.store = main.DataStore(str(Path(self.temp.name) / 'data.json'))
        # Existing installation fixture: no activation watermark, like a 4.3.x upgrade.
        p.store.set('push_groups', [A, B])
        p._scheduled = [row(), row(2, 3)]
        p._schedule_data_ready = True
        p._token, p._fetch_ahead = 'offline-placeholder', 2
        p._scheduled_mids, p._match_tasks = set(), {}
        p._result_tasks, p._result_meta, p._pending_reschedules, p._tournament_tasks = {}, {}, {}, {}
        p.panel = types.SimpleNamespace(push_log=lambda *args: None)
        p.context = object()
        p._format = lambda function, *args, **kwargs: function(*args, **kwargs)
        p._match_with_snapshot_names = deepcopy
        p._build_message_chain = lambda value: value
        self.sent, self.rendered = [], []
        self.clock = NOW
        async def render(text, **kwargs):
            rules = rules_module.current(p.store)
            record = {'text': text, 'group': rules.group_id, 'image': rules.get('image_push_enabled', True),
                      'ids': [m['id'] for m in kwargs.get('matches') or []]}
            self.rendered.append(record)
            return [record]
        p._notification_components = AsyncMock(side_effect=render)
        async def send(context, kind, target, chain, **kwargs):
            self.sent.append((target, self.clock, deepcopy(chain)))
            return {'message_id': len(self.sent)}
        self.sender = send
        for patcher in (patch.object(main, 'send_target', side_effect=send),
                        patch.object(main, 'now_utc', side_effect=lambda: self.clock),
                        patch.object(main, 'now_cst', side_effect=lambda: self.clock.astimezone(timezone(timedelta(hours=8))))):
            patcher.start(); self.addCleanup(patcher.stop)

    def overrides(self, values):
        self.p.store.set('group_rules', values)

    async def advance(self, seconds):
        self.assertGreater(seconds, 0)
        self.clock += timedelta(seconds=seconds)

    def test_individual_inheritance_false_empty_and_copy_isolation(self):
        self.p.store.set('followed_teams', [{'id': 1, 'name': 'NAVI'}])
        self.overrides({A: {'remind_minutes': 23, 'finished_enabled': False, 'followed_teams': [], 'min_tiers': []}})
        a, b = Rules(self.p.store, A), Rules(self.p.store, B)
        self.assertEqual((a.get('remind_minutes'), b.get('remind_minutes')), (23, 10))
        self.assertFalse(a.enabled('finished')); self.assertEqual(a.get_followed_teams(), [])
        self.assertFalse(a.accepts(row()))
        self.p.store.set('remind_minutes', 31)
        self.assertEqual((Rules(self.p.store, A).get('remind_minutes'), Rules(self.p.store, B).get('remind_minutes')), (23, 31))
        a.values['followed_teams'].append({'id': 9, 'name': 'Local'})
        self.assertEqual(Rules(self.p.store, A).get_followed_teams(), [])

    def test_validation_boundaries_and_identity_restrictions(self):
        for override in ({'remind_minutes': 0}, {'remind_minutes': 121}, {'remind_minutes': True},
                         {'remind_minutes': 2.5}, {'finished_enabled': 'false'}, {'min_tiers': ['x']},
                         {'daily_push_times': ['25:00']}, {'pandascore_token': 'bad'}, {'group_rules': {}},
                         {'push_enabled': None}, {'filter_mode': 'anything'}):
            with self.subTest(override=override), self.assertRaises(config.ConfigError):
                config.validate_config({'group_rules': {A: override}})
        good = config.validate_config({'group_rules': {A: {'remind_minutes': 1}, B: {'remind_minutes': 120, 'followed_teams': []}}})
        self.assertEqual(good['group_rules'][B]['remind_minutes'], 120)
        with self.assertRaises(config.ConfigError):
            self.p.store.import_config({'group_rules': {C: {'remind_minutes': 7}}})

    def test_import_export_reload_preserves_receipts_and_removes_runtime_fields(self):
        self.p.store.mark_delivery_target('upcoming:1', f'group:{A}')
        self.p.store.import_config({'group_rules': {A: {'remind_minutes': 37, 'finished_enabled': False}}})
        reloaded = main.DataStore(self.p.store.path)
        self.assertEqual(Rules(reloaded, A).get('remind_minutes'), 37)
        self.assertEqual(reloaded.get_delivery_targets('upcoming:1'), {f'group:{A}'})
        exported = reloaded.export_all()
        self.assertIn('group_rules', exported)
        self.assertNotIn('group_rule_since', exported); self.assertNotIn('notification_targets', exported)
        self.assertNotIn('delivery_receipts', exported)
        reloaded.import_config({'group_rules': {}})
        self.assertEqual(Rules(reloaded, A).get('remind_minutes'), 10)

    def test_old_config_load_and_failed_import_rollback(self):
        legacy = {'push_groups': [A], 'followed_teams': ['NAVI'], 'remind_minutes': 19,
                  'notified_finished': [7], 'delivery_receipts': {'upcoming:1': [f'group:{A}']}}
        Path(self.p.store.path).write_text(json.dumps(legacy))
        store = main.DataStore(self.p.store.path)
        self.assertEqual(Rules(store, A).get('remind_minutes'), 19)
        self.assertTrue(store.is_finished_notified(7)); self.assertEqual(store.get('group_rules'), {})
        before = deepcopy(store._data)
        with patch.object(store, 'save', side_effect=OSError('offline disk failure')):
            with self.assertRaises(OSError): store.import_config({'group_rules': {A: {'remind_minutes': 9}}})
        self.assertEqual(store._data, before)

    def test_blacklists_precede_modes_and_team_ids(self):
        self.overrides({A: {'filter_mode': 'followed', 'followed_teams': [{'id': 1, 'name': 'Old Name'}], 'min_tiers': []},
                        B: {'filter_mode': 'tiers', 'min_tiers': ['a']}})
        self.assertTrue(Rules(self.p.store, A).accepts(row(tier='c')))
        self.assertFalse(Rules(self.p.store, B).accepts(row(tier='s')))
        self.p.store._data['group_rules'][A]['blacklist_teams'] = ['navi']
        self.assertFalse(Rules(self.p.store, A).accepts(row()))
        self.p.store._data['group_rules'][A]['blacklist_teams'] = []
        self.p.store._data['group_rules'][A]['blacklist_leagues'] = ['test']
        self.assertFalse(Rules(self.p.store, A).accepts(row()))

    async def test_union_tracks_match_only_requested_by_custom_group(self):
        p = self.p; match = row(tier='c')
        self.overrides({B: {'filter_mode': 'followed', 'followed_teams': [{'id': 1, 'name': 'NAVI'}]}})
        p.client = types.SimpleNamespace(get_upcoming_matches=AsyncMock(return_value=[match]))
        starts = []
        p._start_match_remind_task = lambda match, minutes: starts.append(match['id'])
        p._schedule_daily_image_warm = lambda: None
        await p._fetch_and_schedule_locked()
        self.assertEqual(starts, [1]); self.assertEqual([m['id'] for m in p._scheduled], [1])
        self.assertEqual(rules_module.groups_for(p.store, 'upcoming', match), [B])

    async def test_user_minutes_schedule_each_group_and_keep_one_result_tracker(self):
        p = self.p; match = row()
        self.overrides({A: {'remind_minutes': 23}, B: {'remind_minutes': 7}})
        p.client = types.SimpleNamespace(get_match_result=AsyncMock(side_effect=lambda mid: deepcopy(match)))
        restored = []
        def capture(coroutine, label):
            restored.append(label); coroutine.close(); return types.SimpleNamespace(done=lambda: False)
        p._create_background_task = capture
        p._match_tasks[1] = asyncio.current_task(); p._scheduled_mids.add(1)
        p._push_upcoming = AsyncMock(wraps=p._push_upcoming)
        with patch.object(main.asyncio, 'sleep', side_effect=self.advance):
            await p._schedule_match(deepcopy(match), 10)
        self.assertEqual([(gid, int((when - NOW).total_seconds() // 60)) for gid, when, _ in self.sent], [(A, 37), (B, 53)])
        self.assertEqual([call.args[1] for call in p._push_upcoming.await_args_list], [23, 7])
        self.assertEqual(restored, ['result:1'])
        self.assertTrue(p.store.is_upcoming_notified(1))

    async def test_single_match_override_precedes_group_minutes(self):
        self.overrides({A: {'remind_minutes': 23}, B: {'remind_minutes': 7}})
        self.p.store.set_custom_remind(1, 41)
        self.assertEqual([self.p.group_notifications.reminder_time(row(), gid)[1] for gid in (A,B)], [41,41])

    async def test_reminder_disabled_for_one_group_result_still_sent(self):
        p = self.p; match = row()
        self.overrides({A: {'upcoming_enabled': False}, B: {'remind_minutes': 17}})
        p.client = types.SimpleNamespace(get_match_result=AsyncMock(return_value=deepcopy(match)))
        with patch.object(main.asyncio, 'sleep', side_effect=self.advance): await p._run_remind(deepcopy(match), 10)
        self.assertEqual([gid for gid,_,_ in self.sent], [B])
        await p.group_notifications.finished({**match, 'status': 'finished', 'results': []})
        self.assertEqual([gid for gid,_,_ in self.sent], [B,A,B])

    async def test_result_failure_retries_only_failed_group_and_freezes_audience(self):
        p = self.p; match = {**row(), 'status': 'finished'}
        async def fail(context, kind, target, chain, **kwargs):
            if target == B: raise RuntimeError('offline failure')
            return await self.sender(context, kind, target, chain, **kwargs)
        with patch.object(main, 'send_target', side_effect=fail):
            self.assertFalse(await p.group_notifications.finished(match))
        p.store.add_group(C)
        self.assertTrue(await p.group_notifications.finished(match))
        self.assertEqual([gid for gid,_,_ in self.sent], [A,B])
        self.assertEqual(p.store.get_delivery_targets('finished:1'), {f'group:{A}', f'group:{B}'})

    async def test_paused_group_rechecked_after_render(self):
        p = self.p
        async def render(text, **kwargs):
            gid = rules_module.selected_group()
            p.store._data['group_rules'].setdefault(gid, {})['push_enabled'] = False
            return ['offline']
        p._notification_components = AsyncMock(side_effect=render)
        self.assertFalse(await p.group_notifications.finished({**row(), 'status': 'finished'}))
        self.assertEqual(self.sent, [])

    async def test_scope_isolated_during_concurrent_group_rendering(self):
        p = self.p
        self.overrides({A: {'image_push_enabled': True}, B: {'image_push_enabled': False}})
        original_sleep = asyncio.sleep
        async def render(text, **kwargs):
            before = rules_module.current(p.store)
            await original_sleep(0)
            after = rules_module.current(p.store)
            self.assertEqual(before.group_id, after.group_id)
            return [{'group': after.group_id, 'image': after.get('image_push_enabled')}]
        p._notification_components = AsyncMock(side_effect=render)
        await asyncio.gather(*(p.group_notifications.deliver(f'finished:1', gid, 'finished',
            lambda: p._push('offline'), match=row()) for gid in (A,B)))
        self.assertEqual({gid: content[0]['image'] for gid,_,content in self.sent}, {A:True,B:False})
        self.assertTrue(p.store.get('image_push_enabled'))

    async def test_reschedule_uses_each_groups_own_previous_receipt(self):
        p = self.p; match = row(minutes=300)
        p.store.mark_delivery_target('upcoming:1', f'group:{A}')
        notices.queue_change(p, match, (NOW + timedelta(hours=4)).isoformat(), NOW)
        self.assertEqual(p._pending_reschedules[1].audience, (A,))
        with patch.object(notices, 'components', new=AsyncMock(return_value=['offline'])):
            await notices.send_pending(p, [match], NOW)
        self.assertEqual([gid for gid,_,_ in self.sent], [A])

    async def test_reschedule_batch_threshold_is_per_group_and_mode_is_scoped(self):
        p = self.p; matches = [row(1),row(2,3),row(3,3)]
        self.overrides({B:{'filter_mode':'followed','followed_teams':[{'id':1,'name':'NAVI'}], 'image_push_enabled':False}})
        for match in matches: notices.queue_change(p, match, (NOW + timedelta(minutes=30)).isoformat(), NOW)
        renders=[]
        async def render(plugin, changes):
            rules=rules_module.current(plugin.store)
            renders.append((rules.group_id,len(changes),rules.get('image_push_enabled')))
            return [m.match['id'] for m in changes]
        with patch.object(notices, 'components', side_effect=render): await notices.send_pending(p,matches,NOW)
        self.assertEqual(renders,[(A,3,True),(B,1,False)])
        self.assertFalse(p._pending_reschedules)

    async def test_daily_times_and_contents_are_independent(self):
        p=self.p
        self.overrides({A:{'filter_mode':'followed','followed_teams':[{'id':1,'name':'NAVI'}], 'daily_push_enabled':True,'daily_push_times':['08:00']},
                        B:{'filter_mode':'followed','followed_teams':[{'id':3,'name':'Spirit'}], 'daily_push_enabled':True,'daily_push_times':['09:00']}})
        await p.group_notifications.daily_tick(NOW,NOW-timedelta(minutes=1))
        self.assertEqual([(gid,chain[0]['ids']) for gid,_,chain in self.sent],[(A,[1])])
        self.clock=NOW+timedelta(hours=1)
        await p.group_notifications.daily_tick(self.clock,NOW)
        self.assertEqual([(gid,chain[0]['ids']) for gid,_,chain in self.sent],[(A,[1]),(B,[2])])
        await p.group_notifications.daily_tick(self.clock,self.clock-timedelta(minutes=1))
        self.assertEqual(len(self.sent),2)

    async def test_pending_daily_retry_freezes_content_and_survives_reload(self):
        p=self.p
        self.overrides({A:{'daily_push_enabled':True},B:{'daily_push_enabled':True}})
        async def fail(context,kind,target,chain,**kwargs):
            if target==B:raise RuntimeError('offline failure')
            return await self.sender(context,kind,target,chain,**kwargs)
        with patch.object(main,'send_target',side_effect=fail):
            await p.group_notifications.daily_tick(NOW,NOW-timedelta(minutes=1))
        p.store=main.DataStore(p.store.path)
        p._scheduled=[row(99)]
        self.clock+=timedelta(minutes=1)
        await p.group_notifications.daily_tick(self.clock,NOW)
        self.assertEqual([(gid,chain[0]['ids']) for gid,_,chain in self.sent],[(A,[1,2]),(B,[1,2])])

    async def test_legacy_completed_daily_is_not_replayed(self):
        p=self.p; p.store.set('daily_push_enabled',True)
        p.store.mark_daily_push_status('daily:2026-11-03_08:00','complete')
        p.group_notifications.migrate_daily(NOW)
        await p.group_notifications.daily_tick(NOW,NOW-timedelta(minutes=1))
        self.assertEqual(self.sent,[])
        self.assertEqual(p.store.get_daily_push_status(f'daily:{A}:2026-11-03_08:00'),'complete')

    async def test_legacy_partial_daily_migrates_receipts(self):
        p=self.p;p.store.set('daily_push_enabled',True)
        key='daily:2026-11-03_08:00'
        p.store.mark_daily_push_status(key,'pending',{'text':'legacy','matches':p._scheduled,'days':1})
        p.store.mark_delivery_target(key,f'group:{A}')
        p.group_notifications.migrate_daily(NOW)
        await p.group_notifications.daily_tick(NOW,NOW-timedelta(minutes=1))
        self.assertEqual([gid for gid,_,_ in self.sent],[B])

    async def test_new_or_changed_group_does_not_backfill_elapsed_daily(self):
        p=self.p;p.store.set('daily_push_enabled',True)
        p.store.set('group_rule_since',{A:(NOW+timedelta(seconds=1)).isoformat(),B:(NOW+timedelta(seconds=1)).isoformat()})
        self.clock=NOW+timedelta(minutes=1)
        await p.group_notifications.daily_tick(self.clock,NOW-timedelta(minutes=1))
        self.assertEqual(self.sent,[])

    async def test_opening_hours_independent_with_one_shared_fetch_stream(self):
        p=self.p
        self.overrides({A:{'tournament_announce_hours':3}, B:{'tournament_announce_hours':1}})
        event={'id':'44','name':'Test Cup','begin_at':(NOW+timedelta(hours=4)).isoformat(),
               'league':{'name':'Test League'},'tier':'s','teams':[{'id':1,'name':'NAVI'},{'id':2,'name':'G2'}]}
        p.client=types.SimpleNamespace(get_event=AsyncMock(return_value=event))
        with patch.object(main.asyncio,'sleep',side_effect=self.advance):await p.group_notifications.opening(event)
        self.assertEqual([(gid,int((when-NOW).total_seconds()/3600)) for gid,when,_ in self.sent],[(A,1),(B,3)])
        self.assertEqual(p.client.get_event.await_count,2)
        self.assertTrue(p.store.is_series_notified('44'))

    async def test_manual_group_selection_does_not_require_automatic_daily(self):
        self.overrides({A:{'push_enabled':False},B:{'daily_push_enabled':False,'filter_mode':'followed','followed_teams':[{'id':3,'name':'Spirit'}]}})
        await self.p._do_instant_push(1)
        self.assertEqual([(gid,chain[0]['ids']) for gid,_,chain in self.sent],[(B,[2])])

    async def test_chat_and_preview_use_selected_group_without_sending(self):
        p=self.p
        self.overrides({A:{'filter_mode':'followed','followed_teams':[{'id':3,'name':'Spirit'}]}})
        event=types.SimpleNamespace(get_group_id=lambda:A,get_platform_name=lambda:'aiocqhttp',
                                    plain_result=lambda text:text,chain_result=lambda components:components)
        result=await p._schedule_query_result(event,1)
        self.assertEqual(result[0]['ids'],[2])
        self.assertEqual(result[0]['group'],A)
        preview=await previews.build_preview(p,{'kind':'schedule','days':1,'group_id':A})
        self.assertIn('Spirit',preview['text']);self.assertNotIn('NAVI',preview['text'])
        self.assertEqual(self.sent,[])
        official=types.SimpleNamespace(get_group_id=lambda:A,get_platform_name=lambda:'qq_official')
        self.assertIsNone(p.query_rules(official).group_id)
        with self.assertRaises(config.ConfigError):await previews.build_preview(p,{'group_id':C})

    async def test_selected_rule_test_still_sends_only_to_test_target(self):
        p=self.p;p.store.set('test_target_type','private');p.store.set('test_target_id','999999')
        with scope(Rules(p.store,A)):
            await p._push_test_components(['offline test'])
        self.assertEqual([gid for gid,_,_ in self.sent],['999999'])

    async def test_api_auth_save_export_and_explicit_group_routing(self):
        from aiohttp.test_utils import TestClient, TestServer
        p=self.p
        p.store.set('web_panel_token','offline-panel-token')
        p.reload_runtime_config=AsyncMock();p.rebuild_all_match_tasks=Mock();p.rebuild_tournament_tasks=Mock()
        p.schedule_refresh=Mock();p.schedule_instant_push=Mock()
        panel=web.WebPanel(p);p.panel=panel
        client=TestClient(TestServer(panel.app));await client.start_server()
        self.addAsyncCleanup(client.close)
        self.assertEqual((await client.get('/api/config')).status,401)
        headers={'Authorization':'Bearer offline-panel-token'}
        response=await client.patch('/api/config',json={'group_rules':{A:{'remind_minutes':29}}},headers=headers)
        self.assertEqual(response.status,200)
        p.rebuild_all_match_tasks.assert_called_once();p.rebuild_tournament_tasks.assert_called_once()
        response=await client.get('/api/config/export',headers=headers)
        exported=(await response.json())['data']
        self.assertEqual(exported['group_rules'][A]['remind_minutes'],29)
        self.assertNotIn('web_panel_token',exported)
        response=await client.post('/api/push_now',json={'group_id':B,'days':1},headers=headers)
        self.assertEqual(response.status,200)
        p.schedule_instant_push.assert_called_once_with(1,'web_push_now',group_id=B)
        response=await client.post('/api/push_now',json={'group_id':C},headers=headers)
        self.assertEqual(response.status,400)
        self.assertEqual((await client.get('/group-rules.js')).status,200)

    async def test_new_group_can_join_before_its_future_reminder_without_replaying_sent_group(self):
        p=self.p; match=row()
        p.store.set('push_groups',[A])
        self.overrides({A:{'remind_minutes':23},B:{'remind_minutes':7}})
        p.client=types.SimpleNamespace(get_match_result=AsyncMock(return_value=deepcopy(match)))
        with patch.object(main.asyncio,'sleep',side_effect=self.advance):await p._run_remind(deepcopy(match),10)
        self.assertEqual([gid for gid,_,_ in self.sent],[A])
        self.clock=NOW+timedelta(minutes=40)
        p.store.set('push_groups',[A,B]);p.store.set('group_rule_since',{B:self.clock.isoformat()})
        with patch.object(main.asyncio,'sleep',side_effect=self.advance):await p._run_remind(deepcopy(match),10)
        self.assertEqual([gid for gid,_,_ in self.sent],[A,B])
        self.assertEqual(self.sent[-1][1],NOW+timedelta(minutes=53))

    async def test_reminder_activation_skips_already_elapsed_trigger(self):
        p=self.p;match=row()
        self.overrides({A:{'remind_minutes':23},B:{'upcoming_enabled':False}})
        self.clock=NOW+timedelta(minutes=40)
        p.store.set('group_rule_since',{A:self.clock.isoformat()})
        p.client=types.SimpleNamespace(get_match_result=AsyncMock(return_value=deepcopy(match)))
        with patch.object(main.asyncio,'sleep',side_effect=self.advance):await p._run_remind(deepcopy(match),10)
        self.assertEqual(self.sent,[])

    async def test_one_result_poll_retains_partial_receipts_across_retries(self):
        p=self.p;match={**row(),'status':'running'}
        p.store.mark_upcoming_notified(1)
        p.client=types.SimpleNamespace(get_match_result=AsyncMock(return_value={**match,'status':'finished'}))
        attempts={B:0}
        async def send(context,kind,target,chain,**kwargs):
            if target==B:
                attempts[B]+=1
                if attempts[B]==1:raise RuntimeError('offline failure')
            return await self.sender(context,kind,target,chain,**kwargs)
        with patch.object(main,'send_target',side_effect=send),patch.object(main.asyncio,'sleep',side_effect=self.advance):
            await p._run_result_poll(match)
        self.assertEqual([gid for gid,_,_ in self.sent],[A,B])
        self.assertEqual(p.client.get_match_result.await_count,2)
        self.assertTrue(p.store.is_finished_notified(1));self.assertFalse(p.store.get('pending_results'))

    async def test_history_end_time_excludes_newly_activated_group(self):
        p=self.p
        p.store.set('group_rule_since',{B:(NOW+timedelta(minutes=30)).isoformat()})
        self.clock=NOW+timedelta(hours=1)
        self.assertTrue(await p.group_notifications.finished({**row(),'status':'finished','end_at':(NOW+timedelta(minutes=10)).isoformat()}))
        self.assertEqual([gid for gid,_,_ in self.sent],[A])

    async def test_failed_audience_save_sends_nothing_then_recovers(self):
        p=self.p
        with patch.object(p.store,'save',side_effect=OSError('offline disk failure')):
            self.assertFalse(await p.group_notifications.finished({**row(),'status':'finished'}))
        self.assertEqual(self.sent,[]);self.assertNotIn('finished:1',p.store.get('notification_targets'))
        self.assertTrue(await p.group_notifications.finished({**row(),'status':'finished'}))
        self.assertEqual([gid for gid,_,_ in self.sent],[A,B])

    async def test_paused_group_keeps_query_inventory_without_automatic_send(self):
        p=self.p;match=row(tier='c')
        self.overrides({A:{'push_enabled':False,'filter_mode':'followed','followed_teams':[{'id':1,'name':'NAVI'}]}})
        self.assertTrue(rules_module.any_match(p.store,match))
        self.assertNotIn(A,rules_module.groups_for(p.store,'finished',match))
        event=types.SimpleNamespace(get_group_id=lambda:A,get_platform_name=lambda:'aiocqhttp',
                                    plain_result=lambda text:text,chain_result=lambda components:components)
        p._scheduled=[match]
        result=await p._schedule_query_result(event,1)
        self.assertEqual(result[0]['ids'],[1]);self.assertEqual(self.sent,[])

    async def test_opening_can_be_enabled_only_for_one_group(self):
        p=self.p;p.store.set('tournament_announce_enabled',False)
        self.overrides({A:{'tournament_announce_enabled':True}})
        event={'id':'44','name':'Test Cup','begin_at':(NOW+timedelta(hours=4)).isoformat(),
               'league':{'name':'Test League'},'tier':'s','teams':[{'id':1,'name':'NAVI'}]}
        p.client=types.SimpleNamespace(get_upcoming_events=AsyncMock(return_value=[event]))
        tasks=[]
        def capture(coroutine,label):
            tasks.append(label);coroutine.close();return types.SimpleNamespace(done=lambda:False)
        p._create_background_task=capture
        await p._check_tournament_announces()
        self.assertEqual(tasks,['series:44'])

    async def test_actual_daily_image_cache_separates_group_content_and_text_mode(self):
        from PIL import Image
        import hashlib
        cache_module=importlib.import_module('cs_start_checked.daily_image_cache')
        p=self.p
        p._daily_images=cache_module.DailyImageCache(Path(self.temp.name)/'default')
        p._group_daily_images=cache_module.DailyImageCache(Path(self.temp.name)/'groups')
        p.team_logos=types.SimpleNamespace(get=lambda name:None)
        p._notification_components=types.MethodType(main.CSMatchPlugin._notification_components,p)
        p._resolve_team_marks=AsyncMock(return_value=[]);p._resolve_event_marks=AsyncMock(return_value=[])
        p._team_text_components=AsyncMock(return_value=['text fallback'])
        def render(text,path,**kwargs):
            digest=hashlib.sha256(text.encode()).digest()
            Image.new('RGB',(2,2),tuple(digest[:3])).save(path)
            return path
        p._banner_renderer=types.SimpleNamespace(render_notification=render)
        p._message_component_types=lambda:(None,None,types.SimpleNamespace(fromBytes=lambda data:data))
        p._make_image_file_component=lambda kind,path:path
        self.overrides({A:{'filter_mode':'followed','followed_teams':[{'id':1,'name':'NAVI'}]},
                        B:{'filter_mode':'followed','followed_teams':[{'id':3,'name':'Spirit'}]}})
        data=[]
        for gid in (A,B,A):
            with scope(Rules(p.store,gid)):
                text,matches=p._daily_image_request(1)
                components=await p._notification_components(text,matches=matches,daily_days=1)
                data.append(components[0])
        self.assertTrue(data[0].startswith(b'\x89PNG'));self.assertNotEqual(data[0],data[1]);self.assertEqual(data[0],data[2])
        self.assertEqual(set(p._group_daily_images.windows),{f'g{A}_d1',f'g{B}_d1'})
        p.store._data['group_rules'][B]['image_push_enabled']=False
        with scope(Rules(p.store,B)):
            text,matches=p._daily_image_request(1)
            self.assertEqual(await p._notification_components(text,matches=matches,daily_days=1),['text fallback'])

    async def test_major_image_override_works_when_default_image_mode_is_off(self):
        module=importlib.import_module('cs_start_checked.bracket_images')
        images=module.BracketImages.__new__(module.BracketImages)
        stage={'id':'1','key':'knockout','kind':'knockout','matches':[]}
        images.ready=types.SimpleNamespace(snapshot={'stages':[stage]},stages=[stage],data=None,images=False,token='offline')
        images._usable=Mock(return_value=True);images._image=AsyncMock(return_value=b'PNG bytes')
        snapshot,stages,data=await images.get('knockout',images_enabled=True)
        self.assertEqual(data,b'PNG bytes');self.assertEqual(images._image.await_args.kwargs,{'enabled':True})
        snapshot,stages,data=await images.get('knockout',images_enabled=False)
        self.assertIsNone(data)

    def test_remove_group_and_reset_receipts_persist_cleanly(self):
        p=self.p
        self.overrides({A:{'remind_minutes':29}})
        p.store.freeze_targets('upcoming:1',[A,B])
        p.store.clear_upcoming_notified(1)
        self.assertNotIn('upcoming:1',main.DataStore(p.store.path).get('notification_targets'))
        p.store.remove_group(A)
        reloaded=main.DataStore(p.store.path)
        self.assertNotIn(A,reloaded.get('group_rules'));self.assertNotIn(A,reloaded.get_groups())

    async def test_invalid_actual_start_time_keeps_known_match_notification_safe(self):
        match={**row(),'status':'running','begin_at':'invalid'}
        self.assertTrue(await self.p._maybe_push_started_notice(match))
        self.assertEqual([gid for gid,_,_ in self.sent],[A,B])
        self.assertTrue(all('开赛时间待确认' in chain[0]['text'] for _,_,chain in self.sent))
