"""Shared, restrained game-hub styling for the local Major charts."""
from PIL import Image, ImageDraw, ImageStat

from .team_logos import normalize_name

BG, PANEL, ROW = '#10161d', '#232c36', '#161e27'
INK, MUTED, FAINT = '#edf0f2', '#a2afbc', '#6d7d8d'
GOLD, GREEN, RED = '#e9bd79', '#79d3b0', '#e59b9c'
LINE = '#35414d'


class MajorTheme:
    def __init__(self, shared):
        self.shared = shared

    def text(self, draw, value, x, y, size=16, color=INK, center=False, width=None):
        value = str(value)
        font = (self.shared._fit_banner_font(draw, value, width, size, max(11, size-3))
                if width else self.shared._pick_banner_font(size))
        if width:
            while value and draw.textlength(value, font=font) > width:
                value = value[:-2]+'…' if len(value) > 1 else ''
        self.shared._draw_card_text(draw, value, x, y, font, color, centered=center)

    def mark(self, image, draw, team, x, y, marks, size=28):
        name = str(team.get('name') or '')
        data = marks.get(normalize_name(name)) or marks.get(normalize_name(team.get('acronym')))
        logo = self.shared._notification_logo(data.get('image') if data else None, size)
        if logo is not None:
            channels = ImageStat.Stat(logo.convert('RGB'), mask=logo.getchannel('A')).mean
            brightness = sum(v*w for v, w in zip(channels, (0.2126, 0.7152, 0.0722)))
            if brightness < 70 and max(channels) < 100:
                draw.rounded_rectangle((x-2, y-2, x+size+2, y+size+2), radius=5, fill='#c8d0d7')
            image.paste(logo, (int(x+(size-logo.width)/2), int(y+(size-logo.height)/2)), logo)
        else:
            draw.ellipse((x+1, y+1, x+size-1, y+size-1), fill='#2c3742', outline='#475360')
            self.text(draw, '?' if not name else name[:1].upper(), x+size/2, y+size/2-7,
                      14, MUTED, True)

    def canvas(self, width, height, snapshot, mode):
        image = Image.new('RGB', (width, height), BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, 112), fill='#1b232c')
        draw.rectangle((40, 27, 44, 42), fill=GOLD)
        self.text(draw, 'COUNTER-STRIKE 2', 56, 26, 15, GOLD)
        self.text(draw, '/   MAJOR', 243, 26, 15, MUTED)
        self.text(draw, snapshot['title'], 40, 58, 30, width=width-360)
        self.text(draw, mode, width-152, 66, 17, GOLD, True)
        draw.line((40, 111, width-40, 111), fill=LINE)
        draw.line((40, height-53, width-40, height-53), fill=LINE)
        footer = ('虚拟数据预览 · 比分与晋级结果仅作演示' if snapshot.get('demo') else
                  '仅展示已公布对阵与确认赛果 · 未公布对手保留待定')
        self.text(draw, footer, 40, height-32, 13, GOLD if snapshot.get('demo') else MUTED)
        self.text(draw, '北京时间  /  '+snapshot['fetched_at'], width-270, height-32, 13, MUTED)
        return image, draw
