"""Native Pillow advancement charts using the existing CS card visual system."""
from PIL import Image

from .formatters import fmt_time, translate_match_stage
from .bracket_data import bracket_team_name
from .team_logos import normalize_name
from .major_theme import MajorTheme, BG, ROW, INK, MUTED, FAINT, GOLD, GOLD_WASH, GREEN, GREEN_WASH, RED


class BracketRenderer(MajorTheme):
    CARD_W, CARD_H, GAP_X, GAP_Y, PAD = 360, 152, 112, 28, 40

    def __init__(self, shared_renderer):
        self.shared = shared_renderer

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
        # Small rounded elbows and no arrowheads keep the bracket subordinate
        # to its teams. The source/target relationships still come from data.
        path = [points[0]]
        for index in range(1, len(points)-1):
            before, corner, after = points[index-1:index+2]
            d1 = abs(corner[0]-before[0])+abs(corner[1]-before[1])
            d2 = abs(after[0]-corner[0])+abs(after[1]-corner[1])
            if not d1 or not d2:
                continue
            radius = min(14, d1/2, d2/2)
            a = tuple(corner[j]+(before[j]-corner[j])*radius/d1 for j in (0,1))
            b = tuple(corner[j]+(after[j]-corner[j])*radius/d2 for j in (0,1))
            path.append(a)
            for step in range(1, 9):
                t = step/8
                path.append(tuple((1-t)**2*a[j]+2*(1-t)*t*corner[j]+t*t*b[j] for j in (0,1)))
        path.append(points[-1])
        if not dashed:
            draw.line(path, fill=color, width=2, joint='curve')
        else:
            for start, end in zip(points, points[1:]):
                distance = int(abs(end[0]-start[0]) + abs(end[1]-start[1]))
                for offset in range(0, distance, 10):
                    stop = min(offset + 5, distance)
                    ratio1, ratio2 = offset / max(1, distance), stop / max(1, distance)
                    p = (start[0]+(end[0]-start[0])*ratio1, start[1]+(end[1]-start[1])*ratio1)
                    q = (start[0]+(end[0]-start[0])*ratio2, start[1]+(end[1]-start[1])*ratio2)
                    draw.line((p, q), fill=color, width=2)

    def _card(self, image, draw, match, x, y, marks):
        caption = translate_match_stage(match.get('name') or '') or '对阵待定'
        final = caption.startswith('总决赛')
        self.panel(draw, x, y, self.CARD_W, self.CARD_H, featured=final)
        self.text(draw, caption, x+16, y+13, 14, GOLD if final else MUTED,
                  width=self.CARD_W-32)
        opponents = [(slot.get('opponent') or {}) for slot in match.get('opponents') or []][:2]
        opponents += [{} for _ in range(2-len(opponents))]
        scores = {str(result.get('team_id')): result.get('score') for result in match.get('results') or []}
        winner = match.get('winner_id') or (match.get('winner') or {}).get('id')
        for index, team in enumerate(opponents):
            row_top = y + 38 + index * 43
            label = bracket_team_name(team)
            is_winner = match.get('status') == 'finished' and winner is not None and team.get('id') is not None and str(team['id']) == str(winner)
            draw.rounded_rectangle((x+8,row_top-1,x+self.CARD_W-8,row_top+37), radius=5,
                                   fill=GREEN_WASH if is_winner else GOLD_WASH if final else ROW)
            if is_winner:
                draw.rounded_rectangle((x+8,row_top+8,x+11,row_top+28), radius=1, fill=GREEN)
            self.mark(image,draw,team,x+20,row_top+4,marks)
            self.text(draw,label,x+62,row_top+11,18,INK if is_winner or match.get('status') != 'finished' else MUTED,
                      width=self.CARD_W-126, bold=is_winner)
            score = scores.get(str(team.get('id')))
            show = match.get('status') in {'running','finished'} and isinstance(score,int) and not isinstance(score,bool) and score >= 0
            self.text(draw, score if show else '—', x+self.CARD_W-30, row_top+10, 19,
                      GREEN if is_winner else MUTED, True, bold=is_winner)
        status = {'finished':'已结束','running':'进行中','canceled':'已取消','postponed':'已延期'}.get(match.get('status'),'待开赛')
        when = fmt_time(match.get('scheduled_at') or match.get('begin_at'))
        bo = f"BO{match['number_of_games']}" if match.get('number_of_games') else ''
        detail = f"{when if when != '未知' else '时间待定'} · {bo} · {status}"
        self.text(draw,detail,x+16,y+133,11,GOLD if match.get('status') == 'running' else FAINT,
                  width=self.CARD_W-32)

    def render(self, snapshot, stages, output_path, team_marks, *, upcoming=False):
        from .swiss_renderer import SwissRenderer
        panels = [SwissRenderer(self.shared).render(snapshot,stage,team_marks,upcoming=upcoming)
                  if stage['kind']=='swiss' and stage.get('swiss_groups')
                  else self._tree_image(snapshot,[stage],team_marks,upcoming=upcoming) for stage in stages]
        if not panels:
            panels=[self._tree_image(snapshot,[],team_marks)]
        width=max(panel.width for panel in panels)
        height=sum(panel.height for panel in panels)
        if width*height>16000000:raise ValueError('bracket image exceeds safe size')
        image=Image.new('RGB',(width,height),BG)
        top=0
        for panel in panels:
            image.paste(panel,((width-panel.width)//2,top))
            top+=panel.height
        image.save(output_path,format='PNG',optimize=True)
        return output_path

    def _tree_image(self, snapshot, stages, team_marks, *, upcoming=False):
        max_columns = max((len(s['columns']) for s in stages), default=1)
        if max_columns > 8:
            raise ValueError('bracket has too many columns')
        width = max(1100, 2*self.PAD + max_columns*self.CARD_W + max(0,max_columns-1)*self.GAP_X)
        layout, cursor = [], 124
        for stage in stages:
            positions, graph_height = self._positions(stage,width,cursor+108)
            if not stage['columns']:
                graph_height = 62
            layout.append((cursor,stage,positions,graph_height))
            cursor += 108+graph_height+50
        height = cursor+54
        if width*height > 16000000:
            raise ValueError('bracket image exceeds safe size')
        image, draw = self.canvas(width,height,snapshot,'PLAYOFFS')
        marks = {normalize_name(mark['name']):mark for mark in team_marks}
        for top,stage,positions,graph_height in layout:
            self.stage_heading(draw, stage['title'], self.PAD, top+10, upcoming)
            note = ' · '.join(stage['notes']) if stage['notes'] else '实线：胜者晋级　虚线：败者转入败者组' if any(e['type']=='loser' for e in stage['edges']) else '按已公布晋级关系连接；未知队伍显示待定'
            self.text(draw,note,self.PAD,top+45,14,MUTED,width=width-80)
            if not positions:
                self.text(draw,'本阶段对阵尚未公布',width//2,top+100,18,MUTED,True)
                continue
            for col_index,column in enumerate(stage['columns']):
                if column['matches']:
                    x=positions[str(column['matches'][0]['id'])][0]
                    self.round_heading(draw, col_index+1, column['label'], x, top+70, self.CARD_W)
            # Draw explicit match-to-match edges beneath the cards. Skip-column
            # links route below the graph rather than through intermediate cards.
            for index,edge in enumerate(stage['edges']):
                sx,sy,sc=positions[edge['source']];tx,ty,tc=positions[edge['target']]
                start=(sx+self.CARD_W,sy+self.CARD_H//2);end=(tx,ty+self.CARD_H//2)
                mid=start[0]+self.GAP_X//2
                if tc-sc>1:
                    lane=top+108+graph_height+12+(index%3)*8
                    points=[start,(mid,start[1]),(mid,lane),(tx-14,lane),(tx-14,end[1]),end]
                else:
                    points=[start,(mid,start[1]),(mid,end[1]),end]
                self._edge(draw, points, GOLD if edge['type']=='winner' else RED, edge['type']=='loser')
            for column in stage['columns']:
                for match in column['matches']:
                    x,y,_=positions[str(match['id'])]
                    self._card(image,draw,match,x,y,marks)
        return image
