"""Major notice identity and the light editorial companion to Major charts."""
import re

from PIL import Image, ImageDraw, ImageFilter

from .cs_major import is_major
from .formatters import fmt_time, reschedule_card_times, sched_str
from .match_stages import match_stage_label
from .tournament_events import event_name


def major_context(*, text='', match=None, competition=None, snapshot=None):
    """Use competition metadata, never tier or the user-facing notice text.

    Feed one combined identity to the existing classifier so a qualifier/RMR
    in any enclosing object vetoes even a Major-named league. A confirmed
    snapshot can fill an incomplete identity only for its own series/match.
    """
    source = match if match is not None else competition
    if not source:
        return None
    # Only these notification formats have a dedicated layout. Query replies
    # and future/unknown formats retain their original renderer.
    copy = re.sub(r'^\s*(?:\[测试\]|【测试】)\s*', '', text)
    heading = re.search(r'【([^】]+)】', copy)
    title = heading[1].removesuffix(' · 关注战队') if heading else ''
    kinds = {'CS2 赛前提醒': 'upcoming', 'CS2 已开赛': 'started',
             'CS2 赛事结果': 'result', 'CS2 赛事开幕通知': 'opening',
             '关注战队参赛提醒': 'opening', 'CS2 赛程变更': 'reschedule'}
    kind = kinds.get(title)
    if kind is None or (kind == 'opening') != (match is None):
        return None
    tournament = source.get('tournament') or {}
    serie = source.get('serie') or tournament.get('serie') or {}
    league = source.get('league') or tournament.get('league') or serie.get('league') or {}
    objects = (source, tournament, serie, league)
    identity = ' '.join(str(obj.get(key) or '') for obj in objects
                        for key in ('name', 'full_name', 'slug'))
    sid = serie.get('id') or source.get('serie_id') or tournament.get('serie_id')
    known = bool(snapshot and snapshot.get('serie_id') and sid is not None
                 and str(sid) == str(snapshot['serie_id']))
    if not known and sid is None and match is not None and source.get('id') is not None:
        known = any(str(row.get('id')) == str(source['id'])
                    for stage in (snapshot or {}).get('stages', [])
                    for row in stage.get('matches', []))
    # A team/match name containing "Major" is not evidence of event identity.
    event_objects = (tournament, serie, league) if match is not None else objects
    event_identity = ' '.join(str(obj.get(key) or '') for obj in event_objects
                              for key in ('name', 'full_name', 'slug'))
    if (not is_major({'name': event_identity + (' Major' if known else '')})
            or not is_major({'name': identity + ' Major'})):
        return None
    title = event_name({'league': league, 'serie': serie})
    if title == '未知赛事':
        title = (snapshot or {}).get('title') or source.get('name') or 'CS Major'
    timing = {}
    if kind == 'reschedule':
        for label, key in (('原定', 'old_time'), ('调整', 'new_time')):
            found = re.search(label+r'[：:]\s*([^\n]+)', text)
            if not found:
                return None
            timing[key] = found[1].strip()
        timing['transition'], timing['day_label'] = reschedule_card_times(
            timing['old_time'], timing['new_time'], sched_str(source),
        )
    return {
        'kind': kind,
        'event': title,
        'stage': match_stage_label(source) if match is not None else '',
        'format': f"BO{source['number_of_games']}" if match is not None and source.get('number_of_games') else '',
        'time': fmt_time(source.get('begin_at') if kind == 'started' else sched_str(source)),
        'end': fmt_time(source.get('end_at')) if competition is not None else '',
        'teams': source.get('teams') or [],
        'draw': bool(source.get('draw')), 'forfeit': bool(source.get('forfeit')),
        **timing,
    }


def stage_context(snapshot, stage, start):
    return {'kind': 'stage', 'event': snapshot['title'], 'stage': stage['title'],
            'time': fmt_time(start.isoformat()), 'demo': bool(snapshot.get('demo'))}


INK, MUTED = '#132033', '#526173'
NAVY, GOLD, LINE = '#132033', '#d9b778', '#ddd9cf'
PAPER, GREEN = '#f4f6f9', '#34785e'


class MajorNoticeRenderer:
    """640px sibling of ordinary notices; navy, paper, ceremonial gold."""
    def __init__(self, shared):
        self.shared = shared

    def text(self, draw, value, x, y, size=16, color=INK, center=False, width=None):
        value = str(value or '')
        font = (self.shared._fit_banner_font(draw, value, width, size, 11)
                if width else self.shared._pick_banner_font(size))
        if width:
            value = self.shared._shorten_banner_text(draw, value, font, width)
        self.shared._draw_card_text(draw, value, x, y, font, color, centered=center)

    def rows(self, draw, value, size=18, width=564):
        return self.shared._wrap_notification_text(draw, str(value or ''), self.shared._pick_banner_font(size), width)

    def trophy(self, draw, x, y, color=GOLD, scale=1):
        # An original cup outline, not an organiser or Valve certification mark.
        for points in (((0,0),(20,0),(17,13),(10,18),(3,13),(0,0)),
                       ((1,3),(-5,3),(-4,10),(4,13)),
                       ((19,3),(25,3),(24,10),(16,13)),
                       ((10,18),(10,25)), ((3,26),(17,26))):
            draw.line([(x+px*scale,y+py*scale) for px,py in points],
                      fill=color, width=max(2,round(2*scale)))

    def backdrop(self, image, header):
        """Quiet spotlights and engraved geometry, kept behind readable copy."""
        draw = ImageDraw.Draw(image)
        for y in range(header):
            t = y / max(header-1, 1)
            draw.line((0,y,639,y), fill=tuple(round(a+(b-a)*t)
                      for a,b in zip((29,45,64),(17,29,46))))
        ornament = Image.new('RGBA', image.size)
        etched = ImageDraw.Draw(ornament)
        for offset in (0, 24, 52):
            etched.line([(370+offset,0),(454+offset,header-1),(639,header-1)],
                        fill=(217,183,120,24), width=1)
        self.trophy(etched, 552, 48, color=(217,183,120,15), scale=4)
        # Clip header ornament so it cannot spill into the match area.
        image.paste(Image.alpha_composite(image.crop((0,0,640,header)).convert('RGBA'),
                    ornament.crop((0,0,640,header))).convert('RGB'), (0,0))
        glow = Image.new('RGBA', image.size)
        light = ImageDraw.Draw(glow)
        light.ellipse((32,header+2,608,header+200), fill=(223,190,132,25))
        glow = glow.filter(ImageFilter.GaussianBlur(28))
        image.paste(Image.alpha_composite(image.convert('RGBA'),glow).convert('RGB'))

    def medallion(self, draw, cx, top, winner=False):
        draw.ellipse((cx-51,top-10,cx+51,top+92), outline='#d3bc92', width=1)
        draw.ellipse((cx-47,top-6,cx+47,top+88), fill='#e9edf1', outline='#ddd9cf')
        for start,end in ((208,258),(282,332),(28,78),(102,152)):
            draw.arc((cx-54,top-13,cx+54,top+95), start,end,
                     fill='#b79457' if winner else '#d7c39f', width=2)
        draw.polygon([(cx,top-17),(cx+3,top-14),(cx,top-11),(cx-3,top-14)], fill=GOLD)

    def render(self, text, output_path, context, *, logo_images=None, team_names=None,
               center_text='VS', team_marks=None, round_winners=None, winner_index=None):
        s = self.shared
        names = [s._notification_text(n) for n in (team_names or [])[:2]]
        kind = context['kind']
        matchup = len(names) == 2 and kind in {'upcoming', 'started', 'result', 'reschedule'}
        result = kind == 'result'
        test = bool(re.match(r'^\s*(?:\[测试\]|【测试】)', text or ''))
        demo = context.get('demo') or '虚拟数据' in text or '虚拟赛事' in context.get('event', '')
        titles = {'upcoming': '赛前提醒', 'started': '已开赛', 'result': '赛事结果',
                  'opening': '赛事开幕', 'stage': '阶段开赛', 'reschedule': '赛程变更'}
        measure = ImageDraw.Draw(Image.new('RGB', (640, 1)))
        event_rows = self.rows(measure, context['event'], 18)
        header = 109 + 25 * len(event_rows)
        stage_rows = self.rows(measure, context.get('stage'), 17, 520) if context.get('stage') else []
        name_rows = [self.rows(measure, name, 19, 190) for name in names] if matchup else []
        outcomes = list(round_winners or []) if result else []
        if result and winner_index is None and not (context.get('draw') or context.get('forfeit')):
            score = re.fullmatch(r'\s*(\d+)\s*:\s*(\d+)\s*', center_text)
            if score and score[1] != score[2]:
                winner_index = 0 if int(score[1]) > int(score[2]) else 1
        if context.get('draw'):
            winner_index = None
        # Preserve meaningful formatter details without repeating its matchup.
        details = []
        if '关注战队' in text:
            details.append('关注战队参赛')
        if result and context.get('draw'):
            details.append('平局')
        elif result and context.get('forfeit'):
            details.append('弃权判定' + (f' · {names[winner_index]} 获胜' if winner_index in (0, 1) else ''))
        for line in (text or '').splitlines():
            line = s._notification_text(line).strip()
            if re.match(r'^(?:举办地点|奖金池)[：:]', line):
                details.extend(self.rows(measure, line, 15))
        if matchup:
            content_height = 185 + 25 * max(map(len, name_rows))
            if outcomes:
                content_height += 36 * ((len(outcomes)+4)//5)
            content_height += 28 * len(stage_rows) + (22 if stage_rows else 0)
        else:
            content_height = (32 * len(stage_rows) + 20 if stage_rows else 0) + 118
            if context.get('end') and context['end'] != '未知':
                content_height += 28
            teams = [team for team in context.get('teams', []) if isinstance(team, dict) and team.get('name')]
            if teams:
                content_height += 54 + 94 * ((len(teams)+3)//4)
            elif kind == 'opening':
                content_height += 32
            if kind == 'stage':
                content_height += 34
        height = header + content_height + 25 * len(details) + 54
        image = Image.new('RGB', (640, height), PAPER)
        draw = ImageDraw.Draw(image)
        self.backdrop(image, header)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 7, header), fill=GOLD)
        draw.line((8,0,639,0), fill='#e6cd9c')
        draw.line((8,header-1,639,header-1), fill='#b29461')
        self.text(draw, 'CS2', 38, 23, 16, '#ff8a70')
        self.text(draw, '/  MAJOR', 82, 24, 14, GOLD)
        self.text(draw, titles[kind], 38, 55, 27, '#ffffff')
        draw.rounded_rectangle((483, 18, 606, 78), radius=7, outline='#514e47')
        draw.rounded_rectangle((487, 22, 602, 74), radius=5, fill='#263241', outline='#aa8d5d')
        self.trophy(draw, 502, 34)
        self.text(draw, 'MAJOR', 539, 43, 13, GOLD)
        if test:
            self.text(draw, '测试推送', 396, 27, 12, GOLD)
        draw.line((38, 96, 602, 96), fill='#45505c')
        draw.line((38, 96, 138, 96), fill=GOLD)
        for index, row in enumerate(event_rows):
            self.text(draw, row, 38, 109+index*25, 18, '#dce5f0')
        y = header
        if matchup:
            self.text(draw, context.get('format'), 320, y+19, 13, MUTED, True)
            logo_y = y+44
            for index, cx in enumerate((153, 487)):
                self.medallion(draw, cx, logo_y, result and index == winner_index)
                mark = {'name': names[index], 'image': (logo_images or [])[index] if index < len(logo_images or []) else None}
                s._draw_small_mark(image, draw, cx-38, logo_y+3, mark, 76, names[index])
                for offset, row in enumerate(name_rows[index]):
                    self.text(draw, row, cx, y+151+offset*25, 19, INK, True)
                if result and index == winner_index:
                    draw.rounded_rectangle((cx-29,y+130,cx+29,y+146), radius=4, fill='#eee3ce', outline='#d9bf90')
                    self.text(draw, '胜者', cx, y+133, 11, '#86652f', True)
            if result:
                self.text(draw, center_text, 320, y+60, 47, INK, True, 170)
                self.text(draw, 'FINAL', 320, y+118, 11, MUTED, True)
                draw.line((293,y+141,347,y+141), fill=GOLD, width=2)
            elif kind == 'reschedule':
                self.text(draw, 'VS', 320, y+46, 23, MUTED, True)
                transition = context['transition']
                label = context['day_label']
                font = s._fit_banner_font(draw, transition, 208, 23, 13)
                s._draw_card_text(draw, transition, 320, y+87, font, INK, centered=True)
                if label:
                    bounds = draw.textbbox((0, 0), transition, font=font)
                    right = 320 + (bounds[2] - bounds[0]) / 2
                    draw.rounded_rectangle((right-30, y+65, right, y+82), radius=4, fill='#eee3ce')
                    self.text(draw, label, right-15, y+68, 11, '#86652f', True)
                self.text(draw, '北京时间', 320, y+119, 11, MUTED, True)
            else:
                self.text(draw, 'VS', 320, y+46, 23, MUTED, True)
                clock = context.get('time', '')
                match_time = re.search(r'([0-2]\d:[0-5]\d)$', clock)
                self.text(draw, match_time[1] if match_time else '待确认', 320, y+83, 33, INK, True, 160)
                self.text(draw, '已开赛 · 北京时间' if kind == 'started' else '北京时间', 320, y+126, 11, MUTED, True)
            y += 185 + 25 * max(map(len, name_rows))
            if outcomes:
                for offset in range(0, len(outcomes), 5):
                    row = outcomes[offset:offset+5]
                    left = 320-len(row)*51
                    for index, winner in enumerate(row):
                        x = left+index*102
                        draw.rounded_rectangle((x,y,x+94,y+26), radius=4, fill='#e5ebef')
                        label = names[winner] if winner in (0,1) else '待确认'
                        self.text(draw, f'{offset+index+1}  {label}', x+47, y+7, 12,
                                  GREEN if winner == winner_index else MUTED, True, 84)
                    y += 36
            if stage_rows:
                draw.line((38,y,602,y), fill=LINE)
                draw.polygon([(316,y),(320,y-4),(324,y),(320,y+4)], fill=GOLD)
                y += 16
                for row in stage_rows:
                    self.text(draw, row, 320, y, 17, MUTED, True)
                    y += 28
                y += 6
        else:
            y += 24
            for row in stage_rows:
                self.text(draw, row, 38, y, 22)
                y += 32
            if stage_rows:
                y += 20
            draw.rounded_rectangle((38,y,602,y+82), radius=5, fill='#eeece6', outline='#d9c9ac')
            draw.line((49,y+4,591,y+4), fill='#faf6ec')
            draw.line((590,y+67,590,y+76,575,y+76), fill='#c6a56b')
            draw.rectangle((38,y+17,41,y+65), fill=GOLD)
            self.text(draw, titles[kind], 58, y+16, 12, MUTED)
            clock = (' '.join(filter(None, (context['transition'], context['day_label'])))
                     if kind == 'reschedule' else context.get('time', '时间待确认'))
            self.text(draw, clock, 58, y+40, 29)
            self.text(draw, '北京时间', 543, y+53, 12, MUTED, True)
            y += 98
            if context.get('end') and context['end'] != '未知':
                self.text(draw, '预计结束  /  '+context['end'], 38, y, 14, MUTED)
                y += 28
            if teams:
                self.text(draw, f'已公布参赛战队  /  {len(teams)}', 38, y+12, 14, MUTED)
                y += 54
                marks = s._team_mark_index(team_marks)
                for index, team in enumerate(teams):
                    cx = 108+(index%4)*141
                    top = y+(index//4)*94
                    mark = marks.get(s._team_key(team['name']))
                    s._draw_small_mark(image, draw, cx-22, top, mark, 44, team['name'])
                    self.text(draw, team['name'], cx, top+58, 13, INK, True, 125)
                y += 94*((len(teams)+3)//4)
            elif kind == 'opening':
                self.text(draw, '参赛名单待公布', 38, y+8, 14, MUTED)
                y += 32
            if kind == 'stage':
                self.text(draw, '本阶段对阵图随附 · 未公布对手保留待定', 38, y+6, 14, MUTED)
                y += 34
        for line in details:
            self.text(draw, line, 320 if matchup else 38, y+2, 15, MUTED, matchup)
            y += 25
        draw.rectangle((0,height-44,640,height), fill='#e5ebf2')
        draw.line((38,height-44,602,height-44), fill=GOLD)
        draw.line((280,height-44,360,height-44), fill='#a88342', width=2)
        self.text(draw, 'CS2 比赛推送  /  MAJOR', 38, height-27, 11, MUTED)
        footer = '虚拟数据预览 · 非真实赛事' if demo else '测试推送' if test else '比赛已结束' if result else 'Major 赛事资讯'
        self.text(draw, footer, 480, height-27, 11, '#8b6a35' if demo else MUTED, True, 240)
        image.save(output_path, format='PNG', optimize=True)
        return output_path
