"""比赛横幅渲染；与调度、网络下载及消息发送分离。"""

import io
import os
from functools import lru_cache

from astrbot.api import logger


class BannerRenderer:
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
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/msyh.ttc",
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

