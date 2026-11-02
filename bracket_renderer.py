"""Native Pillow advancement charts using the existing CS card visual system."""
from PIL import Image, ImageDraw

from .formatters import fmt_time, translate_match_stage
from .bracket_data import bracket_team_name
from .team_logos import normalize_name


class BracketRenderer:
    CARD_W, CARD_H, GAP_X, GAP_Y, PAD = 220, 118, 36, 20, 30

    def __init__(self, shared_renderer):
        self.shared = shared_renderer

    def _text(self, draw, text, x, y, size=16, color='#243447', centered=False):
        self.shared._draw_card_text(draw, str(text), x, y, self.shared._pick_banner_font(size), color,
                                    centered=centered)

    def _positions(self, stage, width, top):
        columns = stage['columns']
        count = max((len(c['matches']) for c in columns), default=1)
        graph_height = count * self.CARD_H + max(0, count - 1) * self.GAP_Y
        total_width = len(columns) * self.CARD_W + max(0, len(columns) - 1) * self.GAP_X
        offset = (width - total_width) // 2
        positions = {}
        for col, column in enumerate(columns):
            aims = []
            for index, match in enumerate(column['matches']):
                parents = [e['source'] for e in stage['edges'] if e['target'] == str(match['id'])]
                if parents and all(p in positions for p in parents):
                    aim = sum(positions[p][1] + self.CARD_H / 2 for p in parents) / len(parents) - self.CARD_H / 2
                elif stage['kind'] == 'swiss':
                    aim = top + index * (self.CARD_H + self.GAP_Y)
                else:
                    n = len(column['matches'])
                    aim = top + ((graph_height - self.CARD_H) * index / (n - 1) if n > 1 else (graph_height - self.CARD_H) / 2)
                aims.append(max(top, aim))
            # Resolve equal parent averages without overlapping cards.
            for i in range(1, len(aims)):
                aims[i] = max(aims[i], aims[i-1] + self.CARD_H + self.GAP_Y)
            if aims and aims[-1] > top + graph_height - self.CARD_H:
                aims[-1] = top + graph_height - self.CARD_H
                for i in range(len(aims)-2, -1, -1):
                    aims[i] = min(aims[i], aims[i+1] - self.CARD_H - self.GAP_Y)
            for match, y in zip(column['matches'], aims):
                positions[str(match['id'])] = (offset + col * (self.CARD_W + self.GAP_X), int(y), col)
        return positions, graph_height

    def _edge(self, draw, points, color, dashed=False):
        if not dashed:
            draw.line(points, fill=color, width=2)
        else:
            for start, end in zip(points, points[1:]):
                distance = int(abs(end[0]-start[0]) + abs(end[1]-start[1]))
                for offset in range(0, distance, 10):
                    stop = min(offset + 5, distance)
                    ratio1, ratio2 = offset / max(1, distance), stop / max(1, distance)
                    p = (start[0]+(end[0]-start[0])*ratio1, start[1]+(end[1]-start[1])*ratio1)
                    q = (start[0]+(end[0]-start[0])*ratio2, start[1]+(end[1]-start[1])*ratio2)
                    draw.line((p, q), fill=color, width=2)
        x, y = points[-1]
        draw.polygon(((x,y),(x-6,y-4),(x-6,y+4)), fill=color)

    def _card(self, image, draw, match, x, y, marks):
        draw.rounded_rectangle((x,y,x+self.CARD_W,y+self.CARD_H), radius=8, fill='#ffffff', outline='#cbd5e1', width=1)
        caption = translate_match_stage(match.get('name') or '') or '对阵待定'
        font = self.shared._fit_banner_font(draw, caption, self.CARD_W - 18, 13, 10)
        self.shared._draw_card_text(draw, caption, x+9, y+8, font, '#405369')
        opponents = [(slot.get('opponent') or {}) for slot in match.get('opponents') or []][:2]
        opponents += [{} for _ in range(2-len(opponents))]
        scores = {str(result.get('team_id')): result.get('score') for result in match.get('results') or []}
        winner = match.get('winner_id') or (match.get('winner') or {}).get('id')
        for index, team in enumerate(opponents):
            row_top = y + 32 + index * 31
            label = bracket_team_name(team)
            known = label != '待定'
            name = team.get('name') if known else '待定'
            is_winner = match.get('status') == 'finished' and winner is not None and team.get('id') is not None and str(team['id']) == str(winner)
            if is_winner:
                draw.rounded_rectangle((x+4,row_top-2,x+self.CARD_W-4,row_top+27), radius=4, fill='#e1f1e9')
            mark = marks.get(normalize_name(name)) or marks.get(normalize_name(team.get('acronym')))
            if known:
                self.shared._draw_small_mark(image,draw,x+10,row_top,mark,24,name)
            else:
                draw.ellipse((x+12,row_top+2,x+32,row_top+22),outline='#cbd5e1',width=1)
                self._text(draw,'?',x+22,row_top+4,12,'#8a99aa',centered=True)
            font = self.shared._fit_banner_font(draw, str(label), self.CARD_W-82, 17, 11)
            self.shared._draw_card_text(draw, str(label), x+42, row_top+3, font, '#243447')
            score = scores.get(str(team.get('id')))
            show = match.get('status') in {'running','finished'} and isinstance(score,int) and not isinstance(score,bool) and score >= 0
            self._text(draw,score if show else '—',x+self.CARD_W-20,row_top+3,17,
                       '#22654f' if is_winner else '#405369',centered=True)
        status = {'finished':'已结束','running':'进行中','canceled':'已取消','postponed':'已延期'}.get(match.get('status'),'待开赛')
        when = fmt_time(match.get('scheduled_at') or match.get('begin_at'))
        bo = f"BO{match['number_of_games']}" if match.get('number_of_games') else ''
        detail = f"{when if when != '未知' else '时间待定'} · {bo} · {status}"
        font = self.shared._fit_banner_font(draw, detail, self.CARD_W-18, 10, 8)
        self.shared._draw_card_text(draw,detail,x+9,y+99,font,'#66758a')

    def render(self, snapshot, stages, output_path, team_marks):
        max_columns = max((len(s['columns']) for s in stages), default=1)
        if max_columns > 8:
            raise ValueError('bracket has too many columns')
        width = max(792, 2*self.PAD + max_columns*self.CARD_W + max(0,max_columns-1)*self.GAP_X)
        layout, cursor = [], 108
        for stage in stages:
            positions, graph_height = self._positions(stage,width,cursor+92)
            if not stage['columns']:
                graph_height = 62
            layout.append((cursor,stage,positions,graph_height))
            cursor += 92+graph_height+56
        height = cursor+42
        if width*height > 16000000:
            raise ValueError('bracket image exceeds safe size')
        image = Image.new('RGB',(width,height),'#f4f6f9')
        draw = ImageDraw.Draw(image)
        marks = {normalize_name(mark['name']):mark for mark in team_marks}
        draw.rectangle((0,0,width,96),fill='#132033')
        draw.rectangle((0,0,8,96),fill='#ff6848')
        self._text(draw,self.shared.game_label,self.PAD,18,16,'#ff8a70')
        self._text(draw,snapshot['title']+' · 晋级对阵图',self.PAD,46,26,'#ffffff')
        for top,stage,positions,graph_height in layout:
            self._text(draw,stage['title'],self.PAD,top+10,23)
            note = ' · '.join(stage['notes']) if stage['notes'] else '实线：胜者晋级　虚线：败者转入败者组' if any(e['type']=='loser' for e in stage['edges']) else '按已公布晋级关系连接；未知队伍显示待定'
            self._text(draw,note,self.PAD,top+43,14,'#66758a')
            if not positions:
                self._text(draw,'本阶段对阵尚未公布',width//2,top+100,18,'#66758a',centered=True)
                continue
            for column in stage['columns']:
                if column['matches']:
                    x=positions[str(column['matches'][0]['id'])][0]
                    self._text(draw,column['label'],x+self.CARD_W//2,top+69,15,'#405369',centered=True)
            # Draw explicit match-to-match edges beneath the cards. Skip-column
            # links route below the graph rather than through intermediate cards.
            for index,edge in enumerate(stage['edges']):
                sx,sy,sc=positions[edge['source']];tx,ty,tc=positions[edge['target']]
                start=(sx+self.CARD_W,sy+self.CARD_H//2);end=(tx,ty+self.CARD_H//2)
                mid=start[0]+self.GAP_X//2
                if tc-sc>1:
                    lane=top+92+graph_height+12+(index%3)*8
                    points=[start,(mid,start[1]),(mid,lane),(tx-14,lane),(tx-14,end[1]),end]
                else:
                    points=[start,(mid,start[1]),(mid,end[1]),end]
                self._edge(draw,points,'#2a8066' if edge['type']=='winner' else '#b47676',edge['type']=='loser')
            for column in stage['columns']:
                for match in column['matches']:
                    x,y,_=positions[str(match['id'])]
                    self._card(image,draw,match,x,y,marks)
        draw.rectangle((0,height-42,width,height),fill='#e5ebf2')
        self._text(draw,'仅展示数据源已公布的对阵与晋级关系',self.PAD,height-27,12,'#405369')
        self._text(draw,'北京时间 · 更新 '+snapshot['fetched_at'],width-245,height-27,12,'#405369')
        image.save(output_path,format='PNG',optimize=True)
        return output_path
