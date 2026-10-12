"""比赛横幅渲染；与调度、网络下载及消息发送分离。"""

import io
import os
import re
from functools import lru_cache

from astrbot.api import logger


class BannerRenderer:
    def render_notification(
        self,
        text: str,
        output_path: str,
        title: str = "",
        logo_images: list[bytes | None] | None = None,
        team_names: list[str] | None = None,
        center_text: str = "VS",
    ) -> str:
        """Render a complete notification as one compact, dynamically sized card.

        The caller supplies downloaded logo bytes and handles delivery/retries.
        Unlike the legacy banner, failure propagates so an image-only delivery
        cannot accidentally be marked successful. Text and URLs wrap without
        truncation; a large schedule grows vertically instead of losing rows.
        """
        from PIL import Image as PILImage, ImageDraw

        names = [self._notification_text(name) for name in (team_names or [])[:2]]
        has_matchup = len(names) == 2
        lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = [self._notification_text(line).strip() for line in lines]
        while lines and not lines[0]:
            lines.pop(0)
        if lines:
            heading = re.match(r"^【([^】]+)】\s*(.*)$", lines[0])
            if heading:
                title = title or heading.group(1)
                lines[0] = heading.group(2)
            elif title and lines[0] == self._notification_text(title):
                lines.pop(0)
        title = self._notification_text(title or "CS2 赛事推送")
        body = []
        for line in lines:
            if line and re.fullmatch(r"[─━—\-＝=\s]+", line):
                continue
            # The matchup is already shown with logos and score in the header.
            if has_matchup and all(name and name in line for name in names):
                if re.search(r"\bvs\b|\s\d+\s*:\s*\d+\s|\s-\s*:\s*-\s", line, re.IGNORECASE):
                    continue
            if not line and (not body or not body[-1]):
                continue
            body.append(line)
        while body and not body[-1]:
            body.pop()

        width, padding = 640, 28
        measure_image = PILImage.new("RGB", (width, 1))
        measure = ImageDraw.Draw(measure_image)
        title_font = self._pick_banner_font(24)
        is_daily = "赛程日报" in title or "每日赛程" in title
        body_size = 18 if len(body) > 18 or is_daily else 20
        body_font = self._pick_banner_font(body_size)
        detail_font = self._pick_banner_font(16)
        small_font = self._pick_banner_font(14)
        title_rows = self._wrap_notification_text(measure, title, title_font, width - 2 * padding - 14)
        title_height = 28 * len(title_rows)
        cursor = 26 + title_height + 24
        matchup = None
        if has_matchup:
            name_font = self._pick_banner_font(18)
            name_rows = [self._wrap_notification_text(measure, name, name_font, 205) for name in names]
            score_font = self._fit_banner_font(measure, str(center_text), 132, 30, 16)
            score_rows = self._wrap_notification_text(measure, self._notification_text(center_text), score_font, 132)
            matchup_height = max(104 + 24 * max(map(len, name_rows)), 36 + 34 * len(score_rows))
            matchup = (cursor, matchup_height, name_rows, name_font, score_rows, score_font)
            cursor += matchup_height + 22

        wrapped = []
        line_height = body_size + 10
        for line in body:
            if not line:
                wrapped.append(("", 12, body_font, 0, "#314457"))
                continue
            schedule_row = re.match(r"^(\d{2}:\d{2})\s+(.+?\s+vs\s+.+?)\s+(BO\d+\s*·\s*.+)$", line, re.IGNORECASE) if is_daily else None
            if schedule_row:
                match_line = f"{schedule_row.group(1)}  {schedule_row.group(2)}"
                for row in self._wrap_notification_text(measure, match_line, body_font, width - 2 * padding):
                    wrapped.append((row, 25, body_font, 0, "#314457"))
                for row in self._wrap_notification_text(measure, schedule_row.group(3), detail_font, width - 2 * padding - 60):
                    wrapped.append((row, 24, detail_font, 60, "#668098"))
                continue
            for row in self._wrap_notification_text(measure, line, body_font, width - 2 * padding):
                color = "#527089" if re.match(r"https?://|.*[：:]\s*https?://", row) else "#314457"
                wrapped.append((row, line_height, body_font, 0, color))
        body_height = sum(row[1] for row in wrapped)
        height = cursor + body_height + 54
        image = PILImage.new("RGB", (width, height), "#f2f5fa")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((10, 10, width - 11, height - 11), radius=20, fill="#ffffff", outline="#e1e7ef")
        draw.rounded_rectangle((padding, 26, padding + 4, 26 + title_height), radius=2, fill="#2584cd")
        for index, row in enumerate(title_rows):
            self._draw_card_text(draw, row, padding + 14, 26 + index * 28, title_font, "#19324d")
        if matchup:
            top, match_height, name_rows, name_font, score_rows, score_font = matchup
            draw.rounded_rectangle((padding, top, width - padding, top + match_height), radius=14, fill="#f1f6fc")
            logo_y = top + 14
            for index, center_x in enumerate((160, 480)):
                data = (logo_images or [])[index] if index < len(logo_images or []) else None
                logo = self._notification_logo(data, 64)
                if logo is not None:
                    # A white logo plate keeps dark team marks visible on blue.
                    draw.rounded_rectangle((center_x - 38, logo_y - 3, center_x + 38, logo_y + 73), radius=14, fill="#ffffff")
                    image.paste(logo, (center_x - logo.width // 2, logo_y + (64 - logo.height) // 2 + 3), logo)
                else:
                    draw.rounded_rectangle((center_x - 32, logo_y + 3, center_x + 32, logo_y + 67), radius=13, fill="#dbe8f5")
                    initial = self._banner_placeholder_text(names[index])
                    initial_font = self._fit_banner_font(draw, initial, 50, 21, 12)
                    self._draw_card_text(draw, initial, center_x, logo_y + 24, initial_font, "#326488", centered=True)
                for row_index, row in enumerate(name_rows[index]):
                    self._draw_card_text(draw, row, center_x, top + 91 + row_index * 24, name_font, "#243b53", centered=True)
            score_top = top + 36
            for index, row in enumerate(score_rows):
                self._draw_card_text(draw, row, width // 2, score_top + index * 34, score_font, "#2584cd", centered=True)
        for row, row_height, font, indent, color in wrapped:
            if row:
                self._draw_card_text(draw, row, padding + indent, cursor, font, color)
            cursor += row_height
        draw.line((padding, height - 42, width - padding, height - 42), fill="#edf0f4")
        self._draw_card_text(draw, "CS2  ·  赛事推送", padding, height - 30, small_font, "#8090a0")
        image.save(output_path, format="PNG", optimize=True)
        return output_path

    @staticmethod
    def _notification_text(text: str) -> str:
        # Color emoji fonts are not portable across Windows and Linux Pillow.
        # Remove decorative icons while preserving the adjacent Chinese labels.
        return re.sub(
            r"[\U0001f000-\U0001faff\u231a\u231b\u23e9-\u23ff\u2600-\u27ff\u2b00-\u2bff\ufe0f\u200d]",
            "",
            str(text or ""),
        )

    @staticmethod
    def _wrap_notification_text(draw, text: str, font, max_width: int) -> list[str]:
        if not text:
            return [""]
        rows, row = [], ""
        for char in text:
            candidate = row + char
            if row and draw.textlength(candidate, font=font) > max_width:
                space = row.rfind(" ")
                if space > len(row) // 2:
                    rows.append(row[:space].rstrip())
                    row = (row[space + 1:] + char).lstrip()
                else:
                    rows.append(row.rstrip())
                    row = char.lstrip()
            else:
                row = candidate
        if row:
            rows.append(row.rstrip())
        return rows or [""]

    @staticmethod
    def _draw_card_text(draw, text, x, y, font, fill, centered=False):
        bbox = draw.textbbox((0, 0), text, font=font)
        if centered:
            x -= (bbox[2] - bbox[0]) / 2
        draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill)

    @staticmethod
    def _notification_logo(data, size):
        if not data:
            return None
        from PIL import Image as PILImage

        try:
            with PILImage.open(io.BytesIO(data)) as source:
                logo = source.convert("RGBA")
            # Transparent padding from the source otherwise makes logos tiny.
            bounds = logo.getbbox()
            if not bounds:
                return None
            logo = logo.crop(bounds)
            logo.thumbnail((size, size), PILImage.Resampling.LANCZOS)
            return logo
        except (OSError, ValueError, PILImage.DecompressionBombError):
            return None

    def render(
        self,
        logo_images: list[bytes | None],
        team_names: list[str],
        center_text: str,
        output_path: str,
        kind: str = "match",
    ) -> str:
        try:
            from PIL import Image as PILImage, ImageDraw
        except ImportError:
            return ""

        try:
            # 赛前图尽量与赛后图保持同样的视觉尺寸
            banner_width = 320
            banner_height = 88
            logo_size = 50
            logo_box = 54
            left_center_x = 82
            right_center_x = 238
            logo_y = 3

            logos = []
            for data in logo_images[:2]:
                canvas = None
                if data:
                    try:
                        logo = PILImage.open(io.BytesIO(data)).convert("RGBA")
                        logo.thumbnail((logo_size, logo_size), PILImage.LANCZOS)
                        canvas = PILImage.new("RGBA", (logo_box, logo_box), (0, 0, 0, 0))
                        offset = ((logo_box - logo.width) // 2, (logo_box - logo.height) // 2)
                        canvas.alpha_composite(logo, offset)
                    except (OSError, ValueError):
                        canvas = None
                logos.append(canvas)
            while len(logos) < 2:
                logos.append(None)

            banner = PILImage.new("RGBA", (banner_width, banner_height), (255, 255, 255, 255))
            draw = ImageDraw.Draw(banner)
            if logos[0] is not None:
                banner.alpha_composite(logos[0], (left_center_x - logos[0].width // 2, logo_y))
            else:
                self._draw_banner_logo_placeholder(draw, left_center_x, logo_y, team_names[0])
            if logos[1] is not None:
                banner.alpha_composite(logos[1], (right_center_x - logos[1].width // 2, logo_y))
            else:
                self._draw_banner_logo_placeholder(draw, right_center_x, logo_y, team_names[1])

            center_max_w = 84
            name_max_w = 112
            center_font = self._fit_banner_font(draw, center_text, center_max_w, 19, 12)
            name_font_left = self._fit_banner_font(draw, team_names[0], name_max_w, 14, 9)
            name_font_right = self._fit_banner_font(draw, team_names[1], name_max_w, 14, 9)
            left_name = self._shorten_banner_text(draw, team_names[0], name_font_left, name_max_w)
            right_name = self._shorten_banner_text(draw, team_names[1], name_font_right, name_max_w)
            center_text = self._shorten_banner_text(draw, center_text, center_font, center_max_w)
            self._draw_centered_text(
                draw, center_text, banner.width // 2, 22, center_font,
                fill=(0, 0, 0, 255),
            )

            self._draw_centered_text(
                draw, left_name, left_center_x, 60, name_font_left,
                fill=(34, 34, 34, 255),
            )
            self._draw_centered_text(
                draw, right_name, right_center_x, 60, name_font_right,
                fill=(34, 34, 34, 255),
            )

            banner.save(output_path, format="PNG")
            return output_path
        except Exception as exc:
            logger.warning(f"[CS] 比赛图片生成失败: {type(exc).__name__}: {exc}")
            return ""

    def _draw_banner_logo_placeholder(self, draw, center_x: int, top_y: int, team_name_text: str):
        left = center_x - 24
        right = center_x + 24
        bottom = top_y + 48
        draw.rounded_rectangle(
            (left, top_y, right, bottom),
            radius=10,
            fill=(245, 245, 245, 255),
            outline=(205, 205, 205, 255),
            width=2,
        )
        placeholder = self._banner_placeholder_text(team_name_text)
        font = self._fit_banner_font(draw, placeholder, 30, 11, 8)
        self._draw_centered_text(draw, placeholder, center_x, top_y + 13, font, fill=(70, 70, 70, 255))

    def _banner_placeholder_text(self, team_name_text: str) -> str:
        tokens = [token for token in str(team_name_text or "").replace("-", " ").split() if token]
        if tokens:
            return "".join(token[0].upper() for token in tokens[:3])
        compact = "".join(ch for ch in str(team_name_text or "") if ch.isalnum())
        if compact:
            return compact[:3].upper()
        return "?"

    @lru_cache(maxsize=32)
    def _pick_banner_font(self, size: int):
        from PIL import ImageFont

        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "..", "plugin_data",
                         "astrbot_plugin_cs_match", "fonts", "NotoSansCJKsc-Regular.otf"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _fit_banner_font(self, draw, text: str, max_width: int, start_size: int, min_size: int):
        for size in range(start_size, min_size - 1, -1):
            font = self._pick_banner_font(size)
            bbox = draw.textbbox((0, 0), text, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                return font
        return self._pick_banner_font(min_size)

    def _shorten_banner_text(self, draw, text: str, font, max_width: int) -> str:
        if not text:
            return ""
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text
        # 每轮缩短原文；在已经带省略号的候选上截断会导致字符串增长而死循环。
        for length in range(len(text) - 1, -1, -1):
            candidate = text[:length].rstrip() + "..."
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                return candidate
        return ""

    def _draw_centered_text(
        self, draw, text: str, center_x: int, top_y: int, font, fill,
        stroke_fill=None, stroke_width: int = 0,
    ):
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        x = center_x - (bbox[2] - bbox[0]) // 2
        draw.text(
            (x, top_y),
            text,
            font=font,
            fill=fill,
            stroke_fill=stroke_fill,
            stroke_width=stroke_width,
        )

