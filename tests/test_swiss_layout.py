"""Targeted offline Swiss model, labels, and render checks."""
from copy import deepcopy
import importlib
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest

from swiss_fixtures import swiss_matches

ROOT=Path(__file__).resolve().parents[1]
package=types.ModuleType('cs_swiss_checked');package.__path__=[str(ROOT)]
sys.modules['cs_swiss_checked']=package
if 'astrbot.api' not in sys.modules:
    sys.modules.setdefault('astrbot',types.ModuleType('astrbot'))
    api=types.ModuleType('astrbot.api');api.logger=logging.getLogger('offline-swiss')
    sys.modules['astrbot.api']=api
labels=importlib.import_module('cs_swiss_checked.match_stages')
model=importlib.import_module('cs_swiss_checked.bracket_data')
fmt=importlib.import_module('cs_swiss_checked.formatters')


class SwissLayoutTests(unittest.TestCase):
    def stage(self,rows):
        return model.build_stage({'id':1,'name':'Stage 1 Swiss'},rows,kind_title=('swiss','第一阶段 · 瑞士轮'))

    def test_complete_stage_has_all_record_groups_and_eight_advancers(self):
        result=self.stage(swiss_matches())
        groups={g['record']:g for g in result['swiss_groups']}
        self.assertEqual([len(groups[r]['teams']) for r in ('3-0','3-1','3-2','0-3','1-3','2-3')],[2,3,3,2,3,3])
        self.assertEqual(sum(len(g['matches']) for g in groups.values()),33)
        self.assertEqual(result['unclassified_matches'],[])
        self.assertEqual(result['edges'],[])

    def test_record_can_follow_confirmed_history_but_not_missing_rounds(self):
        rows=swiss_matches()
        for row in rows:
            number=labels.round_number(row)
            row['name']=f'Swiss Round {number}'
        complete=self.stage(rows)
        self.assertFalse(complete['unclassified_matches'])
        missing=self.stage([row for row in rows if labels.round_number(row)!=1])
        self.assertTrue(missing['unclassified_matches'])
        # An explicit Round 5 still independently establishes the 2-2 group.
        self.assertFalse(any(g['teams'] for g in missing['swiss_groups'] if g['record'] not in ('3-2','2-3')))

    def test_notifications_show_prematch_record_instead_of_map_score(self):
        match=next(row for row in swiss_matches() if '(1-1)' in row['name'])
        match['_stage_title']='第一阶段 · 瑞士轮'
        for text in (fmt.fmt_upcoming(match,10),fmt.fmt_started(match),fmt.fmt_finished(match),
                     fmt.fmt_reschedule(match,'20:00','21:00')):
            self.assertIn('第一阶段 · 瑞士轮 1-1',text)
        self.assertEqual(labels.match_stage_label({'name':'Swiss Round 3: NAVI vs G2','results':match['results']}),
                         '瑞士轮 · 战绩组待确认')

    def test_provider_high_mid_low_names_and_playoff_labels(self):
        for name,record in [('Swiss Round 2 High','1-0'),('Swiss Round 2 Low','0-1'),
                            ('Swiss Round 3 Mid','1-1'),('Swiss Round 4 Low','1-2')]:
            self.assertEqual(labels.swiss_record({'name':name}),record)
        self.assertEqual(labels.match_stage_label({'name':'Quarterfinal 1: A vs B','tournament':{'name':'Playoffs'}}),
                         '淘汰赛 · 四分之一决赛')

    def test_unplayed_or_canceled_matches_do_not_create_terminal_results(self):
        rows=swiss_matches()
        for row in rows:row['status']='not_started'
        result=self.stage(rows)
        self.assertFalse(any(g['teams'] for g in result['swiss_groups']))
        for row in rows:row['status']='canceled'
        self.assertFalse(any(g['teams'] for g in self.stage(rows)['swiss_groups']))

    def test_pillow_renders_complete_board(self):
        from PIL import Image
        renderer=importlib.import_module('cs_swiss_checked.bracket_renderer').BracketRenderer
        shared=importlib.import_module('cs_swiss_checked.banners').BannerRenderer()
        snapshot={'title':'MAJOR DEMO','fetched_at':'11-03 23:00','demo':True}
        stage=self.stage(swiss_matches())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'swiss.png'
            renderer(shared).render(snapshot,[stage],str(path),[])
            with Image.open(path) as image:
                self.assertGreaterEqual(image.width,1600)
                self.assertLessEqual(image.width,2400)
                self.assertGreater(image.height,900)
                self.assertLess(image.width*image.height,16000000)

    def test_daily_image_keeps_one_event_heading_with_distinct_stage_rows(self):
        renderer=importlib.import_module('cs_swiss_checked.banners').BannerRenderer()
        lines=['今天（共 2 场）',
               '20:00 NAVI vs G2 BO1 · Demo Major · 阶段：第一阶段 · 瑞士轮 1-1',
               '21:00 Spirit vs MOUZ BO3 · Demo Major · 阶段：第一阶段 · 瑞士轮 2-2']
        grouped=renderer._group_daily_events(lines)
        self.assertEqual([line for line in grouped if line.startswith('赛事：')],['赛事：Demo Major'])
        self.assertTrue(any('瑞士轮 1-1' in line for line in grouped))
        self.assertTrue(any('瑞士轮 2-2' in line for line in grouped))
