"""Shared visual theme for the local Major charts."""
from PIL import Image, ImageDraw, ImageStat

from .team_logos import normalize_name

BG, PANEL, ROW = '#f6efe1', '#fffdf7', '#f5f0e8'
INK, MUTED, FAINT = '#302619', '#625446', '#716350'
GOLD, GREEN, RED = '#80571d', '#205e44', '#8e3f49'
GOLD_WASH, GREEN_WASH, RED_WASH = '#f3e6c9', '#e5f1e8', '#f5e6e7'
LINE, SHADOW = '#d8c7a8', '#e8dcc6'


class MajorTheme:
    def __init__(self, shared):
        self.shared = shared

    def text(self, draw, value, x, y, size=16, color=INK, center=False, width=None, bold=False):
        value = str(value)
        font = (self.shared._fit_banner_font(draw, value, width, size, max(11, size-3))
                if width else self.shared._pick_banner_font(size))
        if width:
            while value and draw.textlength(value, font=font) > width:
                value = value[:-2]+'…' if len(value) > 1 else ''
        self.shared._draw_card_text(draw, value, x, y, font, color, centered=center, bold=bold)

    def mark(self, image, draw, team, x, y, marks, size=28):
        name = str(team.get('name') or '')
        data = marks.get(normalize_name(name)) or marks.get(normalize_name(team.get('acronym')))
        logo = self.shared._notification_logo(data.get('image') if data else None, size)
        if logo is not None:
            channels = ImageStat.Stat(logo.convert('RGB'), mask=logo.getchannel('A')).mean
            brightness = sum(v*w for v, w in zip(channels, (0.2126, 0.7152, 0.0722)))
            if brightness > 195:
                draw.rounded_rectangle((x-2, y-2, x+size+2, y+size+2), radius=5, fill='#52473e')
            image.paste(logo, (int(x+(size-logo.width)/2), int(y+(size-logo.height)/2)), logo)
        else:
            draw.ellipse((x+1, y+1, x+size-1, y+size-1), fill=GOLD_WASH, outline=LINE)
            self.text(draw, '?' if not name else name[:1].upper(), x+size/2, y+size/2-7,
                      14, MUTED, True)

    @staticmethod
    def panel(draw, x, y, width, height, featured=False):
        draw.rounded_rectangle((x, y+3, x+width, y+height+3), radius=10, fill=SHADOW)
        draw.rounded_rectangle((x, y, x+width, y+height), radius=10,
                               fill='#fff8e7' if featured else PANEL,
                               outline='#bd8a3c' if featured else LINE, width=2 if featured else 1)

    def stage_heading(self, draw, label, x, y, upcoming):
        draw.rounded_rectangle((x, y+2, x+4, y+22), radius=2, fill='#b48337')
        self.text(draw, label, x+15, y, 25)
        if upcoming:
            badge = x+31+draw.textlength(label, font=self.shared._pick_banner_font(25))
            draw.rounded_rectangle((badge, y, badge+92, y+25), radius=5, fill=GOLD_WASH)
            self.text(draw, '尚未开赛', badge+46, y+5, 15, GOLD, True)

    def round_heading(self, draw, number, label, x, y, width):
        draw.rounded_rectangle((x, y, x+30, y+23), radius=5, fill=GOLD_WASH)
        self.text(draw, f'{number:02}', x+15, y+5, 14, GOLD, True)
        self.text(draw, label, x+41, y+4, 15, INK, width=width-49)
        draw.line((x, y+29, x+width, y+29), fill=LINE)

    def canvas(self, width, height, snapshot, mode):
        image = Image.new('RGB', (width, height), BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, 111), fill='#38281f')
        # Facets are clipped by the masthead and keep detail away from the title.
        draw.polygon(((width-345, 0), (width-80, 0), (width-192, 111), (width-457, 111)), fill='#443022')
        draw.polygon(((width-73, 0), (width, 0), (width, 111), (width-185, 111)), fill='#523a28')
        draw.rectangle((0, 109, width, 111), fill='#bb8a42')
        draw.line((40, 109, width-40, 109), fill='#e0b972')
        header_gold = '#efc985'
        draw.line(((44, 23), (40, 23), (40, 27), (45, 31)), fill=header_gold, width=2)
        draw.line(((56, 23), (60, 23), (60, 27), (55, 31)), fill=header_gold, width=2)
        draw.polygon(((43, 21), (57, 21), (56, 29), (52, 34), (48, 34), (44, 29)), fill=header_gold)
        draw.line((50, 34, 50, 39), fill=header_gold, width=2)
        draw.line((45, 39, 55, 39), fill=header_gold, width=2)
        self.text(draw, 'COUNTER-STRIKE 2   /   MAJOR', 70, 23, 16, header_gold)
        self.text(draw, snapshot['title'], 40, 51, 36, '#fff5e0', width=width-360, bold=True)
        draw.rounded_rectangle((width-249, 40, width-40, 72), radius=6,
                               fill='#f4dfb4', outline='#e0b972')
        self.text(draw, mode, width-145, 48, 17, '#57391c', True)
        draw.line((40, height-53, width-40, height-53), fill=LINE)
        footer = ('虚拟数据预览 · 比分与晋级结果仅作演示' if snapshot.get('demo') else
                  '仅展示已公布对阵与确认赛果 · 未公布对手保留待定')
        self.text(draw, footer, 40, height-32, 13, GOLD if snapshot.get('demo') else MUTED)
        self.text(draw, '北京时间  /  '+snapshot['fetched_at'], width-270, height-32, 13, MUTED)
        return image, draw
