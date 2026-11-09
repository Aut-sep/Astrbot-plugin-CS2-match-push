"""Swiss record groups, with outcomes attached to the deciding match groups."""
from .bracket_data import bracket_team_name
from .team_logos import normalize_name
from .swiss_data import CAPACITY
from .major_theme import MajorTheme, ROW, INK, MUTED, FAINT, GOLD, GOLD_WASH, GREEN, GREEN_WASH, RED, RED_WASH


class SwissRenderer(MajorTheme):
    WIDTH, PAD, CELL, GAP, ROW_H, HEAD = 2040, 40, 360, 40, 48, 62
    # Terminal bands belong visually to the groups which decide those outcomes.
    STACKS = ((('0-0',),), (('1-0',), ('0-1',)),
              (('3-0', '2-0'), ('1-1',), ('0-2', '0-3')),
              (('3-1', '2-1'), ('1-2', '1-3')), (('3-2', '2-2', '2-3'),))

    def group_height(self, group):
        return 98 if group['terminal'] else self.HEAD+max(CAPACITY[group['record']], len(group['matches']))*self.ROW_H+38

    def positions(self, groups, top):
        heights = [sum(self.group_height(groups[r]) for pack in column for r in pack)+24*(len(column)-1)
                   for column in self.STACKS]
        height = max(heights)
        positions, packs = {}, []
        for column, stack in enumerate(self.STACKS):
            x, y = self.PAD+column*(self.CELL+self.GAP), top+(height-heights[column])//2
            for pack in stack:
                pack_height = sum(self.group_height(groups[r]) for r in pack)
                packs.append((x, y, pack_height))
                for record in pack:
                    h = self.group_height(groups[record])
                    positions[record] = (x, y, h)
                    y += h
                y += 24
        return positions, packs, height

    def match_row(self, image, draw, match, x, y, marks):
        draw.rounded_rectangle((x+8, y, x+self.CELL-8, y+42), radius=5, fill=ROW)
        teams = [(slot.get('opponent') or {}) for slot in (match or {}).get('opponents') or []][:2]
        teams += [{}]*(2-len(teams))
        scores = {str(r.get('team_id')): r.get('score') for r in (match or {}).get('results') or []}
        winner = (match or {}).get('winner_id') or ((match or {}).get('winner') or {}).get('id')
        status = (match or {}).get('status')
        if status == 'running':
            draw.rounded_rectangle((x+8, y+9, x+11, y+33), radius=1, fill=GOLD)
        for index, team in enumerate(teams):
            self.mark(image, draw, team, x+17 if index == 0 else x+self.CELL-45, y+7, marks)
            won = status == 'finished' and winner is not None and str(team.get('id')) == str(winner)
            color = GREEN if won else MUTED if status == 'finished' or not team else INK
            self.text(draw, bracket_team_name(team), x+99 if index == 0 else x+261,
                      y+13, 15, color, True, width=96)
        left, right = (scores.get(str(team.get('id'))) for team in teams)
        show = status in ('running', 'finished') and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (left, right))
        draw.rounded_rectangle((x+154, y+7, x+206, y+35), radius=5, fill=GOLD_WASH)
        self.text(draw, f'{left} : {right}' if show else 'VS', x+180, y+12,
                  17 if show else 13, GOLD if status == 'running' else INK if show else MUTED, True)

    def group(self, image, draw, group, position, marks):
        x, y, height = position
        record = group['record']
        if group['terminal']:
            advanced = record.startswith('3-')
            color = GREEN if advanced else RED
            draw.rounded_rectangle((x+8, y+8, x+self.CELL-8, y+height-8), radius=5,
                                   fill=GREEN_WASH if advanced else RED_WASH)
            self.text(draw, record+'  /  '+('晋级' if advanced else '淘汰'), x+self.CELL/2, y+16, 17, color, True)
            count = max(group['capacity'], len(group['teams']))
            for index in range(count):
                team = group['teams'][index] if index < len(group['teams']) else {}
                center = x+8+(index+0.5)*(self.CELL-16)/count
                self.mark(image, draw, team, center-14, y+40, marks)
                self.text(draw, bracket_team_name(team), center, y+73, 12, MUTED, True, width=(self.CELL-16)/count-10)
            return
        wins, losses = map(int, record.split('-'))
        category = '晋级 / 淘汰' if wins == losses == 2 else '晋级战' if wins == 2 else '淘汰战' if losses == 2 else '赛前战绩'
        self.text(draw, record, x+18, y+15, 29, bold=True)
        color = GOLD if wins == losses == 2 else GREEN if wins == 2 else RED if losses == 2 else MUTED
        self.text(draw, category, x+136, y+24, 14, color)
        games = {m.get('number_of_games') for m in group['matches'] if m.get('number_of_games')}
        self.text(draw, 'BO'+str(next(iter(games))) if len(games) == 1 else 'BO—', x+self.CELL-49, y+25, 13, FAINT, True)
        count = max(CAPACITY[record], len(group['matches']))
        for index in range(count):
            match = group['matches'][index] if index < len(group['matches']) else None
            self.match_row(image, draw, match, x, y+self.HEAD+index*self.ROW_H, marks)
        # Explicit record destinations replace external arrows. They never imply pairings.
        route_y = y+height-24
        self.text(draw, f'胜  {wins+1}-{losses}'+(' 晋级' if wins == 2 else ''), x+18, route_y, 12, GREEN)
        self.text(draw, f'负  {wins}-{losses+1}'+(' 淘汰' if losses == 2 else ''), x+218, route_y, 12, RED)

    def render(self, snapshot, stage, team_marks, *, upcoming=False):
        groups = {group['record']: group for group in stage['swiss_groups']}
        positions, packs, graph_height = self.positions(groups, 220)
        unknown = stage.get('unclassified_matches') or []
        unknown_height = 76+((len(unknown)+4)//5)*self.ROW_H if unknown else 0
        height = 220+graph_height+unknown_height+86
        if self.WIDTH*height > 16000000:
            raise ValueError('Swiss chart exceeds safe size')
        image, draw = self.canvas(self.WIDTH, height, snapshot, 'SWISS STAGE')
        marks = {normalize_name(mark['name']): mark for mark in team_marks}
        self.stage_heading(draw, stage['title'], 40, 137, upcoming)
        self.text(draw, '三胜晋级 · 三负淘汰    /    每轮重新配对，胜负标签仅表示战绩去向', 1120, 143, 15, MUTED)
        for column in range(5):
            x = self.PAD+column*(self.CELL+self.GAP)
            self.round_heading(draw, column+1, f'第 {column+1} 轮', x, 178, self.CELL)
        for x, y, h in packs:
            self.panel(draw, x, y, self.CELL, h)
        for record, position in positions.items():
            self.group(image, draw, groups[record], position, marks)
        if unknown:
            y = 220+graph_height+26
            self.text(draw, '战绩组待确认 · 数据不足时不推测分组', 40, y, 17, MUTED)
            for index, match in enumerate(unknown):
                self.match_row(image, draw, match, self.PAD+(index%5)*(self.CELL+self.GAP), y+34+(index//5)*self.ROW_H, marks)
        return image
