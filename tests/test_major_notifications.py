"""Bounded offline checks for Major identity and notice routing."""
from copy import deepcopy
import importlib
import unittest

import test_start_notifications as fixture

notices = importlib.import_module('cs_start_checked.major_notifications')


class MajorNoticeIdentityTests(unittest.TestCase):
    def context(self, match, title='CS2 赛前提醒', snapshot=None):
        return notices.major_context(text=f'【{title}】', match=match, snapshot=snapshot)

    def test_series_and_league_identity_with_plain_match_name(self):
        for identity in ({'serie': {'full_name': 'Demo Major 2026'}},
                         {'serie': {'slug': 'demo-major-2026'}},
                         {'league': {'name': 'Demo Major'}}):
            with self.subTest(identity=identity):
                match = {**deepcopy(fixture.MATCH), 'name':'Swiss Round 3', **identity}
                self.assertIsNotNone(self.context(match))

    def test_qualifiers_and_ordinary_s_tier_never_receive_major_style(self):
        ordinary = deepcopy(fixture.MATCH)
        self.assertIsNone(self.context(ordinary))
        ordinary['name'] = 'Major Team vs Another Team'
        self.assertIsNone(self.context(ordinary))
        for name in ('Major Qualifier', 'European RMR', 'Minor', 'Major 预选赛', 'Road to Major'):
            with self.subTest(name=name):
                match = {**ordinary, 'serie': {'id':99,'full_name':'Demo Major'},
                         'tournament': {'name':name,'tier':'s'}}
                self.assertIsNone(self.context(match, snapshot={'serie_id':99}))

    def test_confirmed_snapshot_only_identifies_its_own_series(self):
        match = {**deepcopy(fixture.MATCH), 'serie': {'id':99}}
        self.assertIsNotNone(self.context(match,snapshot={'serie_id':99}))
        self.assertIsNone(self.context(match,snapshot={'serie_id':100}))

    def test_unknown_query_and_malformed_reschedule_keep_original_rendering(self):
        match = {**deepcopy(fixture.MATCH), 'serie': {'name':'Demo Major'}}
        for title in ('CS2 赛程变更','CS2 比赛详情','已安排的 CS2 赛程','未知通知'):
            self.assertIsNone(self.context(match,title))
        self.assertEqual(self.context(match,'CS2 已开赛')['kind'],'started')
        self.assertEqual(self.context(match,'CS2 赛事结果 · 关注战队')['kind'],'result')

    def test_reschedule_preserves_both_dates_and_times(self):
        match = {**deepcopy(fixture.MATCH), 'serie': {'name':'Demo Major'}}
        text = fixture.fmt.fmt_reschedule(match,'11-10 20:00','11-11 02:30')
        context = notices.major_context(text=text,match=match)
        self.assertEqual(context['kind'],'reschedule')
        self.assertEqual((context['old_time'],context['new_time']),('11-10 20:00','11-11 02:30'))

    def test_opening_uses_competition_context_and_start_uses_actual_time(self):
        match = {**deepcopy(fixture.MATCH), 'serie': {'full_name':'Demo Major'}}
        self.assertEqual(self.context(match,'CS2 已开赛')['time'],'11-02 18:17')
        context = notices.major_context(text='【CS2 赛事开幕通知】',competition=match)
        self.assertEqual(context['kind'],'opening')
        self.assertIsNone(self.context(match,'CS2 赛事开幕通知'))


if __name__ == '__main__':
    unittest.main()
