"""Game-hub-inspired Swiss record board, rendered locally with Pillow."""
from PIL import Image, ImageDraw, ImageStat
from .bracket_data import bracket_team_name
from .team_logos import normalize_name
from .swiss_data import CAPACITY

BG, PANEL, ROW = '#0b1420', '#172536', '#0e1b2b'
INK, MUTED, GREEN, RED = '#edf3fa', '#98aabd', '#53d6a4', '#ed7b87'


class SwissRenderer:
    WIDTH, PAD, CELL, GAP, ROW_H, HEAD = 1840, 40, 304, 60, 42, 48
    STACKS = (('0-0',), ('1-0', '0-1'), ('3-0', '2-0', '1-1', '0-2', '0-3'),
              ('3-1', '2-1', '1-2', '1-3'), ('3-2', '2-2', '2-3'))

    def __init__(self, shared):
        self.shared = shared

    def text(self, draw, value, x, y, size=16, color=INK, center=False, width=None):
        value = str(value)
        font = (self.shared._fit_banner_font(draw, value, width, size, max(10, size-4))
                if width else self.shared._pick_banner_font(size))
        if width and draw.textbbox((0, 0), value, font=font)[2] > width:
            while value and draw.textbbox((0, 0), value+'…', font=font)[2] > width:
                value = value[:-1]
            value += '…'
        self.shared._draw_card_text(draw, value, x, y, font, color, centered=center)

    def mark(self, image, draw, team, x, y, marks, size=24):
        name = str(team.get('name') or '')
        data = marks.get(normalize_name(name)) or marks.get(normalize_name(team.get('acronym')))
        logo = self.shared._notification_logo(data.get('image') if data else None, size)
        if logo is not None:
            channels=ImageStat.Stat(logo.convert('RGB'),mask=logo.getchannel('A')).mean
            brightness=sum(value*weight for value,weight in zip(channels,(0.2126,0.7152,0.0722)))
            if brightness<70 and max(channels)<100:
                draw.rounded_rectangle((x-2,y-2,x+size+2,y+size+2),radius=4,fill='#c9d3df')
            image.paste(logo, (int(x+(size-logo.width)/2), int(y+(size-logo.height)/2)), logo)
        else:
            draw.ellipse((x+2, y+2, x+size-2, y+size-2), fill='#2a3b4f')
            self.text(draw, '?' if not name else name[:1].upper(), x+size/2, y+5, 12, MUTED, True)

    def group_height(self, group):
        return 96 if group['terminal'] else self.HEAD+max(CAPACITY[group['record']], len(group['matches']))*self.ROW_H+8

    def positions(self, groups, top):
        heights = [sum(self.group_height(groups[key]) for key in stack)+22*(len(stack)-1)
                   for stack in self.STACKS]
        height = max(heights)
        result = {}
        for column, stack in enumerate(self.STACKS):
            y = top+(height-heights[column])//2
            for record in stack:
                h = self.group_height(groups[record])
                result[record] = (self.PAD+column*(self.CELL+self.GAP), y, h)
                y += h+22
        return result, height

    def flow(self, draw, source, target, color, *, terminal=False):
        sx, sy, sh = source
        tx, ty, th = target
        if terminal:
            x = sx+self.CELL/2
            start, end = ((x, sy), (x, ty+th)) if ty < sy else ((x, sy+sh), (x, ty))
            draw.line((start, end), fill=color, width=2)
            direction = -1 if ty < sy else 1
            draw.polygon([(end[0], end[1]), (end[0]-4, end[1]-direction*6),
                          (end[0]+4, end[1]-direction*6)], fill=color)
            return
        # These are record-group routes, not claimed future match pairings.
        start = (sx+self.CELL, sy+sh*(0.35 if color==GREEN else 0.65))
        end = (tx, ty+th/2)
        middle = start[0]+self.GAP*(0.35 if color==GREEN else 0.65)
        draw.line([start,(middle,start[1]),(middle,end[1]),end],fill=color,width=2)
        draw.polygon([(end[0],end[1]),(end[0]-7,end[1]-4),(end[0]-7,end[1]+4)],fill=color)

    def match_row(self, image, draw, match, x, y, marks):
        draw.rounded_rectangle((x+7,y,x+self.CELL-7,y+35),radius=5,fill=ROW)
        teams = [(slot.get('opponent') or {}) for slot in (match or {}).get('opponents') or []][:2]
        teams += [{}]*(2-len(teams))
        scores = {str(r.get('team_id')):r.get('score') for r in (match or {}).get('results') or []}
        winner = (match or {}).get('winner_id') or ((match or {}).get('winner') or {}).get('id')
        status = (match or {}).get('status')
        if status == 'running':
            draw.rectangle((x+7,y+4,x+10,y+31),fill='#f5bd62')
        for index, team in enumerate(teams):
            icon_x = x+15 if index==0 else x+self.CELL-39
            self.mark(image,draw,team,icon_x,y+5,marks)
            label = bracket_team_name(team)
            won = status=='finished' and winner is not None and str(team.get('id'))==str(winner)
            color = GREEN if won else MUTED if status=='finished' else INK
            self.text(draw,label,x+45 if index==0 else x+self.CELL-48,y+10,15,color,
                      width=79,center=False) if index==0 else self.text(draw,label,x+222,y+10,15,color,True,width=83)
        left,right=(scores.get(str(team.get('id'))) for team in teams)
        show=status in ('running','finished') and all(isinstance(v,int) and not isinstance(v,bool) for v in (left,right))
        middle=f'{left}:{right}' if show else 'VS'
        self.text(draw,middle,x+self.CELL/2,y+9,17,'#f5bd62' if status=='running' else INK,True)

    def group(self,image,draw,group,position,marks):
        x,y,height=position
        record=group['record']
        terminal=group['terminal']
        accent=GREEN if record.startswith('3-') else RED if record.endswith('-3') else '#8298b1'
        draw.rounded_rectangle((x,y,x+self.CELL,y+height),radius=8,fill=PANEL,outline='#2b3e53')
        if terminal:
            self.text(draw,f"{record}  {'晋级' if record.startswith('3-') else '淘汰'}",x+self.CELL/2,y+9,19,accent,True)
            count=max(group['capacity'],len(group['teams']))
            for index in range(count):
                team=group['teams'][index] if index<len(group['teams']) else {}
                center=x+(index+0.5)*self.CELL/count
                self.mark(image,draw,team,center-12,y+37,marks)
                self.text(draw,bracket_team_name(team),center,y+68,12,MUTED,True,width=self.CELL/count-8)
            return
        wins,losses=map(int,record.split('-'))
        category='晋级战' if wins==2 else '淘汰战' if losses==2 else '同战绩对阵'
        if wins==losses==2:category='晋级 / 淘汰'
        self.text(draw,record,x+16,y+12,24)
        self.text(draw,category,x+self.CELL-16-82,y+18,13,GREEN if wins==2 else RED if losses==2 else MUTED)
        count=max(CAPACITY[record],len(group['matches']))
        for index in range(count):
            match=group['matches'][index] if index<len(group['matches']) else None
            self.match_row(image,draw,match,x,y+self.HEAD+index*self.ROW_H,marks)

    def render(self,snapshot,stage,team_marks):
        groups={group['record']:group for group in stage['swiss_groups']}
        positions,graph_height=self.positions(groups,190)
        unknown=stage.get('unclassified_matches') or []
        unknown_height=70+((len(unknown)+4)//5)*self.ROW_H if unknown else 0
        height=190+graph_height+unknown_height+70
        if self.WIDTH*height>16000000:raise ValueError('Swiss chart exceeds safe size')
        image=Image.new('RGB',(self.WIDTH,height),BG);draw=ImageDraw.Draw(image)
        marks={normalize_name(mark['name']):mark for mark in team_marks}
        draw.rectangle((0,0,self.WIDTH,104),fill='#152334')
        draw.rectangle((0,0,7,104),fill='#efb45b')
        self.text(draw,'COUNTER-STRIKE 2  /  MAJOR HUB',40,18,15,'#efb45b')
        self.text(draw,snapshot['title'],40,49,28,width=1390)
        self.text(draw,stage['title'],40,121,24)
        self.text(draw,'绿色：获胜 / 晋级    红色：失利 / 淘汰',self.WIDTH-390,126,15,MUTED)
        for column in range(5):
            self.text(draw,f'第 {column+1} 轮',self.PAD+column*(self.CELL+self.GAP)+self.CELL/2,163,14,MUTED,True)
        for record in ('0-0','1-0','0-1','2-0','1-1','0-2','2-1','1-2','2-2'):
            wins,losses=map(int,record.split('-'))
            for target,color in ((f'{wins+1}-{losses}',GREEN),(f'{wins}-{losses+1}',RED)):
                self.flow(draw,positions[record],positions[target],color,terminal=groups[target]['terminal'])
        for record,position in positions.items():self.group(image,draw,groups[record],position,marks)
        if unknown:
            y=190+graph_height+24
            self.text(draw,'战绩组待确认 · 数据不足时不推测分组',40,y,17,MUTED)
            for index,match in enumerate(unknown):
                self.match_row(image,draw,match,self.PAD+(index%5)*(self.CELL+self.GAP),y+34+(index//5)*self.ROW_H,marks)
        draw.rectangle((0,height-46,self.WIDTH,height),fill='#152334')
        footer='虚拟数据预览 · 所有比分、赛程与晋级结果均为演示' if snapshot.get('demo') else '仅展示已公布对阵与确认赛果 · 未公布对手保留待定'
        self.text(draw,footer,40,height-30,14,'#efb45b' if snapshot.get('demo') else MUTED)
        self.text(draw,'赛前战绩 ≠ 单场比分   /   北京时间 '+snapshot['fetched_at'],self.WIDTH-465,height-30,13,MUTED)
        return image
