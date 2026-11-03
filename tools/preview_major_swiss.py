"""Render fictional data through the production renderers, without AstrBot or network."""
from copy import deepcopy
import importlib
import json
import logging
from pathlib import Path
import sys
import types
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from swiss_fixtures import TEAMS, swiss_matches, knockout_matches

package=types.ModuleType('preview_cs');package.__path__=[str(ROOT)];sys.modules['preview_cs']=package
sys.modules.setdefault('astrbot',types.ModuleType('astrbot'))
api=types.ModuleType('astrbot.api');api.logger=logging.getLogger('offline-preview');sys.modules['astrbot.api']=api
data=importlib.import_module('preview_cs.bracket_data')
fmt=importlib.import_module('preview_cs.formatters')
BannerRenderer=importlib.import_module('preview_cs.banners').BannerRenderer
BracketRenderer=importlib.import_module('preview_cs.bracket_renderer').BracketRenderer
catalog=importlib.import_module('preview_cs.team_logos').TeamLogoCatalog()
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
shared=BannerRenderer();renderer=BracketRenderer(shared)
marks=[{'name':team['name'],'image':catalog.get(team['name'])} for team in TEAMS]
snapshot={'title':'MAJOR DEMO 2026 · 虚拟赛事','fetched_at':'11-03 23:00','demo':True}
stages=[]
for number in (1,2,3):
    rows=swiss_matches(number)
    if number==2:
        # Round 4 is published; round 5 has not been drawn.
        rows=[row for row in rows if 'Round 5 ' not in row['name']]
        for row in rows:
            if 'Round 4 ' in row['name']:
                row['status']='not_started';row.pop('winner',None);row['results']=[]
    stage=data.build_stage({'id':number,'name':f'Stage {number} Swiss'},rows,
                           kind_title=('swiss',f"第{'一二三'[number-1]}阶段 · 瑞士轮"))
    stages.append(stage)
    renderer.render(snapshot,[stage],str(out/f'swiss-stage-{number}.png'),marks)
playoffs=data.build_stage({'id':4,'name':'Playoffs'},knockout_matches(),kind_title=('knockout','淘汰赛 · 八强至总决赛'))
renderer.render(snapshot,[playoffs],str(out/'playoffs.png'),marks)
match=deepcopy(next(row for row in stages[0]['matches'] if row.get('_swiss_record')=='1-1'))
match['_stage_title']='第一阶段 · 瑞士轮'
match['league']['name']='MAJOR DEMO 2026（虚拟数据）'
names=[slot['opponent']['name'] for slot in match['opponents']]
images=[catalog.get(name) for name in names]
texts=[]
for kind in ('upcoming','result'):
    text=fmt.fmt_upcoming(match,10) if kind=='upcoming' else fmt.fmt_finished(match)
    text+='\n虚拟数据预览 · 非真实比赛'
    texts.append(text)
    scores={r['team_id']:r['score'] for r in match['results']}
    center='VS' if kind=='upcoming' else ' : '.join(str(scores[slot['opponent']['id']]) for slot in match['opponents'])
    shared.render_notification(text,str(out/f'notice-{kind}.png'),logo_images=images,team_names=names,
                               start_time='20:00' if kind=='upcoming' else '',center_text=center)
left,right=(Image.open(out/f'notice-{kind}.png').convert('RGB') for kind in ('upcoming','result'))
contact=Image.new('RGB',(left.width+right.width+28,max(left.height,right.height)+62),'#e6ecf3')
draw=ImageDraw.Draw(contact)
shared._draw_card_text(draw,'赛前提醒 · 瑞士轮 1-1',22,18,shared._pick_banner_font(20),'#132033')
shared._draw_card_text(draw,'赛后结果 · 保留赛前战绩组',left.width+50,18,shared._pick_banner_font(20),'#132033')
contact.paste(left,(0,62));contact.paste(right,(left.width+28,62));contact.save(out/'notification-preview.png')
(out/'virtual-data.json').write_text(json.dumps({'snapshot':snapshot,'stages':stages,'playoffs':playoffs},ensure_ascii=False,indent=2),encoding='utf-8')
(out/'notification-text.txt').write_text('\n\n'.join(texts),encoding='utf-8')
print(json.dumps({'output':str(out),'images':[p.name for p in out.glob('*.png')],
                  'team_marks':sum(bool(mark['image']) for mark in marks)},ensure_ascii=False))
