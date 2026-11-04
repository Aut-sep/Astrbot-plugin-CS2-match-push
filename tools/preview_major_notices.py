"""Offline virtual notices through the production notification pipeline."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import AsyncMock

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
# Reuse the offline host shim; no live AstrBot, account or network is loaded.
from test_start_notifications import main, fmt
from swiss_fixtures import TEAMS, swiss_matches

notices = importlib.import_module('cs_start_checked.major_notifications')
data = importlib.import_module('cs_start_checked.bracket_data')
BracketRenderer = importlib.import_module('cs_start_checked.bracket_renderer').BracketRenderer
TeamLogoCatalog = importlib.import_module('cs_start_checked.team_logos').TeamLogoCatalog


async def preview(output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        p = main.CSMatchPlugin.__new__(main.CSMatchPlugin)
        p.store = main.DataStore(str(Path(temporary)/'state.json'))
        p._banner_renderer = main.BannerRenderer()
        p._banner_dir = str(output)
        p.team_logos = TeamLogoCatalog()
        p._match_with_snapshot_names = deepcopy
        p._resolve_team_marks = AsyncMock(side_effect=lambda teams: [
            {'name': team['name'], 'image': p.team_logos.get(team['name'])} for team in teams])
        p._resolve_event_marks = AsyncMock(return_value=[])
        p._message_component_types = lambda: (object, str, object)
        p._make_image_file_component = lambda image_type, path: path
        match = deepcopy(swiss_matches()[0])
        match.update(name='Swiss Round 3 (1-1): NAVI vs Vitality', _stage_title='第一阶段 · 瑞士轮',
                     _swiss_record='1-1', status='not_started', number_of_games=3,
                     scheduled_at='2026-11-10T12:00:00Z', begin_at='2026-11-10T12:03:00Z',
                     league={'name': 'MAJOR DEMO'}, serie={'id': 99, 'name': '2026 · 虚拟赛事'},
                     opponents=[{'opponent': TEAMS[0]}, {'opponent': TEAMS[1]}],
                     results=[{'team_id': 1, 'score': 2}, {'team_id': 2, 'score': 1}],
                     winner=TEAMS[0], games=[{'position': index+1, 'winner': {'id': ident}, 'status': 'finished'}
                                            for index, ident in enumerate((1,2,1))])
        suffix = '\n虚拟数据预览 · 非真实比赛'
        cards = []
        for kind in ('upcoming', 'result', 'started'):
            text = (fmt.fmt_finished(match) if kind == 'result' else fmt.fmt_started(match)
                    if kind == 'started' else fmt.fmt_upcoming(match, 10)) + suffix
            await p._notification_components(text, match=match, output_path=str(output/f'major-{kind}.png'),
                                             center_text='2 : 1' if kind == 'result' else 'VS',
                                             started_notice=kind == 'started')
            cards.append(({'upcoming':'Major · 赛前','result':'Major · 赛后','started':'Major · 已开赛补报'}[kind], f'major-{kind}.png'))
        event = {'id':99, 'name':'MAJOR DEMO 2026', 'league':match['league'], 'serie':match['serie'],
                 'tier':'s', 'begin_at':'2026-11-10T12:00:00Z', 'end_at':'2026-11-25T15:00:00Z', 'teams':TEAMS}
        await p._notification_components(fmt.fmt_tournament_announce(event, [], 2)+suffix,
                                         competition=event, teams=event['teams'],
                                         output_path=str(output/'major-opening.png'))
        cards.append(('Major · 赛事开幕', 'major-opening.png'))
        await p._notification_components(fmt.fmt_reschedule(match, '11-10 20:00', '11-11 02:30')+suffix,
                                         match=match, output_path=str(output/'major-reschedule.png'))
        cards.append(('Major · 跨日改期', 'major-reschedule.png'))
        rows = swiss_matches(2)[:8]
        for row in rows:
            row.update(status='not_started', results=[], winner=None)
        stage = data.build_stage({'id':2,'name':'Stage 2 Swiss'}, rows,
                                 kind_title=('swiss','第二阶段 · 瑞士轮'))
        snapshot = {'title':'MAJOR DEMO 2026 · 虚拟赛事', 'fetched_at':'11-14 16:00', 'demo':True,
                    'serie_id':99, 'stages':[stage]}
        start = datetime(2026,11,14,10,tzinfo=timezone.utc)
        p._banner_renderer.render_notification(suffix, str(output/'major-stage.png'),
                                              major=notices.stage_context(snapshot,stage,start))
        marks = await p._resolve_team_marks(TEAMS)
        BracketRenderer(p._banner_renderer).render(snapshot, [stage], str(output/'stage-chart.png'), marks)
        cards.append(('Major · 阶段开赛（另附单阶段对阵图）','major-stage.png'))
        ordinary = deepcopy(match)
        ordinary.update(league={'name':'普通赛事演示（虚拟数据）'}, serie={'name':'2026'},
                        tournament={'name':'Playoffs','tier':'s'}, name='Semifinal', _stage_title='淘汰赛 · 半决赛')
        ordinary.pop('_swiss_record',None)
        await p._notification_components(fmt.fmt_upcoming(ordinary, 10)+suffix, match=ordinary,
                                         output_path=str(output/'ordinary-control.png'))
        cards.append(('普通 S 级赛事 · 对照','ordinary-control.png'))
        # 2 columns, at native resolution: legible standalone review artifact.
        ordered = [cards[0],cards[1],cards[3],cards[5],cards[2],cards[4],cards[6]]
        images = [Image.open(output/name).convert('RGB') for _,name in ordered]
        row_heights = [max(image.height for image in images[i:i+2])+56 for i in range(0,len(images),2)]
        contact = Image.new('RGB',(1308,sum(row_heights)+28),'#dce3eb')
        draw = ImageDraw.Draw(contact)
        y=14
        for index,(label,_) in enumerate(ordered):
            col=index%2
            if index and col==0:
                y+=row_heights[index//2-1]
            p._banner_renderer._draw_card_text(draw,label,col*668+18,y+8,
                                               p._banner_renderer._pick_banner_font(17),'#132033')
            contact.paste(images[index],(col*668,y+42))
        contact.save(output/'preview.png')
        (output/'virtual-data.json').write_text(json.dumps({'match':match,'event':event,'snapshot':snapshot},
                                                           ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'output':str(output),'cards':[name for _,name in cards],
                          'local_team_logos':sum(bool(mark['image']) for mark in marks)},ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(preview(Path(sys.argv[1])))
