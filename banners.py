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
        *,
        start_time: str = "",
        team_marks: list[dict] | None = None,
        round_winners: list[int] | None = None,
        winner_index: int | None = None,
    ) -> str:
        """Render one compact editorial card; delivery failures propagate.

        Logos are supplied by the caller. Image copy omits stream links and
        countdowns, while schedule rows grow vertically without truncation.
        ``start_time`` is a Beijing-time HH:MM displayed beneath VS/the score.
        """
        from PIL import Image as PILImage, ImageDraw, ImageStat

        names = [self._notification_text(name) for name in (team_names or [])[:2]]
        marks = self._team_mark_index(team_marks)
        has_matchup = len(names) == 2
        lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = [self._notification_text(line).strip() for line in lines]
        while lines and not lines[0]:
            lines.pop(0)
        test_mode = False
        if lines and re.match(r"^(?:\[测试\]|【测试】)", lines[0]):
            test_mode = True
            lines[0] = re.sub(r"^(?:\[测试\]|【测试】)\s*", "", lines[0])
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
        is_result = has_matchup and "赛事结果" in title
        if is_result and winner_index is None:
            scores = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(center_text))
            if scores and scores.group(1) != scores.group(2):
                winner_index = 0 if int(scores.group(1)) > int(scores.group(2)) else 1
        game_outcomes = list(round_winners) if round_winners is not None else []
        if is_result and round_winners is None:
            for line in lines:
                game = re.match(r"^第\d+局[：:]\s*(.+?)\s+获胜", line)
                if game:
                    game_outcomes.append(names.index(game.group(1)) if game.group(1) in names else -1)
        time_match = re.search(r"(?<!\d)([0-2]\d:[0-5]\d)(?!\d)", str(start_time or ""))
        display_time = time_match.group(1) if time_match else ""
        changed_times = {}
        if has_matchup and "赛程变更" in title:
            for line in lines:
                changed = re.match(r"^(原定|调整)[：:].*?([0-2]\d:[0-5]\d)", line)
                if changed:
                    changed_times[changed.group(1)] = changed.group(2)
        time_transition = " → ".join(changed_times[key] for key in ("原定", "调整")) if len(changed_times) == 2 else ""
        if time_transition:
            display_time = changed_times["调整"]
        body = []
        for line in lines:
            if time_transition and re.match(r"^(?:原定|调整)[：:]", line):
                continue
            if is_result and re.match(r"^(?:胜者[：:]|各局详情[：:]|第\d+局[：:]|暂无详细数据)", line):
                continue
            if line and re.fullmatch(r"[─━—\-＝=\s]+", line):
                continue
            # Links remain in the separately delivered text, never in the card.
            if re.search(r"(?:https?|ftp)://|www\.", line, re.IGNORECASE):
                continue
            if re.fullmatch(r"直播\s*[：:]?\s*", line):
                continue
            if re.match(r"^(?:还有|距开赛还有约).*(?:分钟|小时)", line):
                continue
            if re.match(r"^(?:开赛时间|原定|调整)[：:]", line):
                line = re.sub(r"(?<=[：:])\s*\d{2,4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?\s+(?=\d{2}:\d{2})", "", line)
            if has_matchup:
                if all(name and name in line for name in names) and re.search(
                    r"\bvs\b|\s\d+\s*:\s*\d+\s|\s-\s*:\s*-\s", line, re.IGNORECASE
                ):
                    continue
                clock = re.fullmatch(r"(?:开赛时间[：:]\s*)?(?:\d{2,4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?\s+)?([0-2]\d:[0-5]\d)(?:\s*[（(]?\s*北京时间[）)]?)?", line)
                if clock:
                    display_time = display_time or clock.group(1)
                    continue
                # Labelled old and adjusted times deliberately remain in body.
            if not line and (not body or not body[-1]):
                continue
            body.append(line)
        while body and not body[-1]:
            body.pop()

        width, padding = 640, 38
        ink, muted, gold = "#132033", "#405369", "#405369"
        background, rule = "#f4f6f9", "#d3dce7"
        measure = ImageDraw.Draw(PILImage.new("RGB", (width, 1)))
        title_font = self._pick_banner_font(23)
        label_font = self._pick_banner_font(12)
        detail_font = self._pick_banner_font(16)
        is_daily = "赛程日报" in title or "每日赛程" in title
        if is_daily:
            body = self._group_daily_events(body)
        body_font = self._pick_banner_font(18)
        header_context = ""
        if has_matchup:
            while body and not body[0]:
                body.pop(0)
            if body and not re.match(r"^(?:平局|弃权|胜者|赛事名称|参赛战队)[：:]?", body[0]):
                header_context = body.pop(0)
        context_rows = self._wrap_notification_text(measure, header_context, detail_font, 236) if header_context else []
        title_rows = self._wrap_notification_text(measure, title, title_font, 300 if context_rows else width - 2 * padding)
        title_y = 53
        header_height = max(96, title_y + len(title_rows) * 29 + 15, 35 + len(context_rows) * 22 + 15)
        cursor = header_height + 16
        matchup = None
        if has_matchup:
            name_font = self._pick_banner_font(19)
            name_rows = [self._wrap_notification_text(measure, name, name_font, 198) for name in names]
            clean_score = self._notification_text(center_text)
            score_font = self._fit_banner_font(measure, clean_score, 144, 49 if is_result else 27, 16)
            score_rows = self._wrap_notification_text(measure, clean_score, score_font, 144)
            score_height = 32 * len(score_rows)
            match_height = max(115 + 25 * max(map(len, name_rows)), 30 + score_height + (68 if display_time else 0))
            if is_result and game_outcomes:
                match_height += 30
            matchup = (cursor, match_height, name_rows, name_font, score_rows, score_font)
            cursor += match_height + 27

        wrapped = []
        for line in body:
            if not line:
                wrapped.append(("", 11, body_font, 0, muted, False, False))
                continue
            schedule_row = re.match(r"^(\d{2}:\d{2})\s+(.+?)\s+vs\s+(.+?)\s+(BO\d+\s*·\s*.+)$", line, re.IGNORECASE) if is_daily else None
            if schedule_row:
                left_name, right_name = schedule_row.group(2).strip(), schedule_row.group(3).strip()
                left_rows = self._wrap_notification_text(measure, left_name, body_font, 145)
                right_rows = self._wrap_notification_text(measure, right_name, body_font, 145)
                detail_rows = [schedule_row.group(4).split("·", 1)[0].strip()]
                wrapped.append({"kind": "schedule", "time": schedule_row.group(1),
                                "left": left_name, "right": right_name,
                                "left_rows": left_rows, "right_rows": right_rows,
                                "left_mark": marks.get(self._team_key(left_name)),
                                "right_mark": marks.get(self._team_key(right_name)),
                                "detail_rows": detail_rows,
                                "height": max(42, 20 * max(len(left_rows), len(right_rows)) + 10)})
                continue
            if is_daily and line.startswith("赛事："):
                wrapped.append(("", 9, body_font, 0, muted, False, False))
                for row in self._wrap_notification_text(measure, line[3:], body_font, width - 2 * padding):
                    wrapped.append((row, 29, body_font, 0, ink, False, False))
                continue
            participants = re.match(r"^参赛战队[：:]\s*(.+)$", line)
            if participants:
                entries = [entry.strip() for entry in re.split(r"[、,，]", participants.group(1)) if entry.strip()]
                if entries:
                    gallery_entries = [(name, marks.get(self._team_key(name))) for name in entries]
                    wrapped.append(("参赛战队", 27, detail_font, 0, muted, False, False))
                    wrapped.append({"kind": "gallery", "entries": gallery_entries,
                                    "height": self._team_gallery_height(measure, gallery_entries, detail_font)})
                    continue
            day_group = bool(is_daily and re.match(r"^(?:今天|明天|\d{2}-\d{2})[（(]", line))
            detail = bool(re.match(r"^第\d+局[：:]|^各局详情[：:]|暂无详细数据", line))
            prominent = bool(re.match(r"^(?:胜者|弃权|平局|原定|调整)[：:]?", line))
            centered = has_matchup and not detail and not prominent
            font = detail_font if detail or centered else body_font
            color = gold if day_group else ink if prominent else muted if detail or centered else ink
            if day_group and wrapped and (wrapped[-1][0] if isinstance(wrapped[-1], tuple) else True):
                wrapped.append(("", 9, body_font, 0, muted, False, False))
            winner = re.match(r"^(?:胜者|弃权，胜者)[：:]\s*(.+)$", line)
            matched_mark = self._find_team_in_text(line, marks)
            icon_mark = (marks.get(self._team_key(winner.group(1))) or
                         {"name": winner.group(1), "image": None}) if winner else (matched_mark[1] if matched_mark else None)
            rows = self._wrap_notification_text(measure, line, font, width - 2 * padding - (34 if icon_mark else 0))
            for row in rows:
                wrapped.append({"kind": "text", "row": row, "height": 27 if not detail else 25,
                                "font": font, "indent": 34 if icon_mark else 0,
                                "color": color, "centered": centered and not icon_mark, "bold": False,
                                "icon": icon_mark})
        height = cursor + sum(item[1] if isinstance(item, tuple) else item.get("height", 27) for item in wrapped) + 55
        image = PILImage.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width, header_height), fill="#132033")
        draw.rectangle((0, 0, 8, header_height), fill="#ff6848")
        self._draw_card_text(draw, "CS2", padding, 23, self._pick_banner_font(16), "#ff8a70")
        if test_mode:
            self._draw_card_text(draw, "测试推送", width - padding - 48, 18, label_font, "#ff8a70")
        for index, row in enumerate(title_rows):
            self._draw_card_text(draw, row, padding, title_y + index * 29, title_font, "#ffffff")
        for index, row in enumerate(context_rows):
            self._draw_card_text(draw, row, width - padding - 236, 35 + index * 22, detail_font, "#dce5f0")
        if matchup:
            top, match_height, name_rows, name_font, score_rows, score_font = matchup
            logo_y = top + 7
            for index, center_x in enumerate((153, 487)):
                data = (logo_images or [])[index] if index < len(logo_images or []) else None
                logo = self._notification_logo(data, 76)
                if logo is not None:
                    # Bright marks, including yellow ones, need a dark plate.
                    alpha = logo.getchannel("A")
                    rgb = logo.convert("RGB")
                    pale = sum(channel * weight for channel, weight in zip(
                        ImageStat.Stat(rgb, mask=alpha).mean,
                        (0.2126, 0.7152, 0.0722))) > 175
                    if pale:
                        draw.ellipse((center_x - 44, logo_y - 6, center_x + 44, logo_y + 82), fill="#333a40")
                    image.paste(logo, (center_x - logo.width // 2, logo_y + (76 - logo.height) // 2), alpha)
                else:
                    draw.ellipse((center_x - 33, logo_y + 5, center_x + 33, logo_y + 71), outline=rule, width=1)
                    initial = self._banner_placeholder_text(names[index])
                    initial_font = self._fit_banner_font(draw, initial, 50, 22, 12)
                    self._draw_card_text(draw, initial, center_x, logo_y + 28, initial_font, gold, centered=True)
                for row_index, row in enumerate(name_rows[index]):
                    self._draw_card_text(draw, row, center_x, top + 105 + row_index * 25, name_font, ink, centered=True)
                if is_result and game_outcomes and index == winner_index:
                    symbols = " ".join("✓" if winner == index else "✗" if winner in (0, 1) else "?" for winner in game_outcomes)
                    symbol_font = self._pick_result_symbol_font(22)
                    for symbol_size in range(22, 11, -1):
                        symbol_font = self._pick_result_symbol_font(symbol_size)
                        symbol_box = draw.textbbox((0, 0), symbols, font=symbol_font)
                        if symbol_box[2] - symbol_box[0] <= 198:
                            break
                    self._draw_card_text(draw, symbols, center_x, top + 110 + 25 * len(name_rows[index]), symbol_font, "#000000", centered=True)
            score_top = top + (34 if is_result else 14)
            for index, row in enumerate(score_rows):
                self._draw_card_text(draw, row, width // 2, score_top + index * 32, score_font, ink if is_result else muted, centered=True, bold=is_result)
            if display_time:
                time_y = score_top + (48 if is_result else 32) * len(score_rows) + 8
                shown_time = time_transition or display_time
                time_font = self._fit_banner_font(draw, shown_time, 180, 24, 16) if time_transition else self._pick_banner_font(34)
                self._draw_card_text(draw, shown_time, width // 2, time_y, time_font, ink, centered=True, bold=not bool(time_transition))
                self._draw_card_text(draw, "北京时间", width // 2, time_y + 45, label_font, gold, centered=True)
        for item in wrapped:
            if isinstance(item, tuple):
                row, row_height, font, indent, color, centered, bold = item
                if row:
                    self._draw_card_text(draw, row, width // 2 if centered else padding + indent, cursor, font, color, centered=centered, bold=bold)
                cursor += row_height
                continue
            if item["kind"] == "schedule":
                row_height = item["height"]
                self._draw_card_text(draw, item["time"], padding, cursor + 8, detail_font, gold)
                left_edge = width // 2 - 24
                self._draw_small_mark(image, draw, left_edge - 24, cursor + 5, item["left_mark"], 24, item["left"])
                for index, row in enumerate(item["left_rows"]):
                    text_box = draw.textbbox((0, 0), row, font=body_font)
                    self._draw_card_text(draw, row, left_edge - 31 - (text_box[2] - text_box[0]), cursor + 6 + index * 19, body_font, ink)
                self._draw_card_text(draw, "vs", width // 2, cursor + 8, label_font, muted, centered=True)
                right_edge = width // 2 + 24
                self._draw_small_mark(image, draw, right_edge, cursor + 5, item["right_mark"], 24, item["right"])
                for index, row in enumerate(item["right_rows"]):
                    self._draw_card_text(draw, row, right_edge + 31, cursor + 6 + index * 19, body_font, ink)
                self._draw_card_text(draw, item["detail_rows"][0], width - padding - 32, cursor + 8, label_font, muted)
                cursor += row_height
                continue
            if item["kind"] == "gallery":
                cursor = self._draw_team_gallery(image, draw, item["entries"], cursor, padding, width, detail_font, ink, rule)
                continue
            if item.get("icon"):
                self._draw_small_mark(image, draw, padding, cursor + 2, item["icon"], 25, item.get("row", "?"))
            row = item["row"]
            if row:
                self._draw_card_text(draw, row, width // 2 if item["centered"] else padding + item["indent"], cursor, item["font"], item["color"], centered=item["centered"], bold=item["bold"])
            cursor += item["height"]
        draw.rectangle((0, height - 50, width, height), fill="#e5ebf2")
        self._draw_card_text(draw, "CS2 比赛推送", padding, height - 30, label_font, muted)
        self._draw_card_text(draw, "比赛已结束" if is_result else "赛事资讯", width - padding - 72, height - 30, label_font, muted)
        image.save(output_path, format="PNG", optimize=True)
        return output_path

    @staticmethod
    def _group_daily_events(lines):
        """Group by event, keeping each day's matches in their original time order."""
        groups, prefix, day = {}, [], ""
        row_pattern = r"^(\d{2}:\d{2})\s+(.+?)\s+vs\s+(.+?)\s+(BO\d+)\s*·\s*(.+)$"
        for line in lines:
            heading = re.match(r"^(今天|明天|\d{2}-\d{2})[（(]", line)
            if heading:
                day = heading.group(1)
                continue
            row = re.match(row_pattern, line, re.IGNORECASE)
            if row:
                groups.setdefault(row.group(5), {}).setdefault(day, []).append(line)
            elif line:
                prefix.append(line)
        if not groups:
            return lines
        output = prefix
        for event, days in groups.items():
            output.append("赛事：" + event)
            for label, matches in days.items():
                if label:
                    output.append(f"{label}（共 {len(matches)} 场）")
                output.extend(matches)
        return output

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
    def _team_key(name: str) -> str:
        return " ".join(str(name or "").casefold().split())

    def _team_mark_index(self, team_marks):
        result = {}
        for mark in team_marks or []:
            if isinstance(mark, dict):
                name, data = mark.get("name", ""), mark.get("image")
            else:
                continue
            key = self._team_key(name)
            if key:
                result[key] = {"name": self._notification_text(name), "image": data}
        return result

    def _find_team_in_text(self, text, marks):
        folded = self._team_key(text)
        candidates = sorted(marks.items(), key=lambda item: len(item[0]), reverse=True)
        for key, mark in candidates:
            if re.search(r"(?<![\w])" + re.escape(key) + r"(?![\w])", folded):
                return key, mark
        return None

    def _draw_small_mark(self, image, draw, x, y, mark, size, placeholder_name):
        from PIL import ImageDraw

        logo = self._notification_logo(mark.get("image") if mark else None, size)
        if logo is not None:
            alpha = logo.getchannel("A")
            from PIL import ImageStat
            brightness = sum(c * w for c, w in zip(ImageStat.Stat(logo.convert("RGB"), mask=alpha).mean,
                                                   (0.2126, 0.7152, 0.0722)))
            if brightness > 175:
                draw.ellipse((x - 2, y - 2, x + size + 2, y + size + 2), fill="#333a40")
            image.paste(logo, (x + (size - logo.width) // 2, y + (size - logo.height) // 2), alpha)
            return
        draw.ellipse((x, y, x + size, y + size), fill="#f0ece2", outline="#d9d1c0", width=1)
        initial = self._banner_placeholder_text(mark.get("name") if mark else placeholder_name)
        font = self._fit_banner_font(draw, initial, size - 4, max(9, size // 2), 7)
        self._draw_card_text(draw, initial, x + size // 2, y + max(2, size // 4), font, "#a38b5f", centered=True)

    def _draw_team_chip(self, image, draw, x, y, name, mark, icon_size, text_width, font, color):
        self._draw_small_mark(image, draw, x, y, mark, icon_size, name)
        rows = self._wrap_notification_text(draw, name, font, text_width)
        for index, row in enumerate(rows):
            self._draw_card_text(draw, row, x + icon_size + 7, y + 1 + index * 19, font, color)

    def _draw_team_gallery(self, image, draw, entries, y, padding, width, font, color, rule):
        cell_w, cols, icon = 180, 3, 24
        rows = []
        for start in range(0, len(entries), cols):
            batch = entries[start:start + cols]
            row_h = 0
            for col, (name, mark) in enumerate(batch):
                x = padding + col * cell_w
                self._draw_small_mark(image, draw, x, y + 2, mark, icon, name)
                name_rows = self._wrap_notification_text(draw, name, font, cell_w - icon - 14)
                row_h = max(row_h, len(name_rows) * 18)
                for line_index, line in enumerate(name_rows):
                    self._draw_card_text(draw, line, x + icon + 7, y + 4 + line_index * 18, font, color)
            y += max(30, row_h + 8)
        return y

    def _team_gallery_height(self, draw, entries, font):
        total = 0
        for start in range(0, len(entries), 3):
            batch = entries[start:start + 3]
            max_lines = max((len(self._wrap_notification_text(draw, name, font, 180 - 24 - 14))
                             for name, _ in batch), default=1)
            total += max(30, max_lines * 18 + 8)
        return total

    def render_team_strip(self, team_marks, output_path: str) -> str:
        """Render all known team marks as a compact gallery for full text mode."""
        from PIL import Image as PILImage, ImageDraw

        marks = self._team_mark_index(team_marks)
        entries = [(mark["name"], mark) for mark in marks.values()]
        width, padding = 48 + 180 * min(3, max(1, len(entries))), 24
        font = self._pick_banner_font(14)
        measure = ImageDraw.Draw(PILImage.new("RGB", (width, 1)))
        # Three columns keep team names readable while preserving every entry.
        row_heights = []
        for offset in range(0, len(entries), 3):
            row_heights.append(max([max(30, 18 * len(self._wrap_notification_text(
                measure, name, font, 180 - 24 - 14)) + 8) for name, _ in entries[offset:offset + 3]] or [30]))
        height = 32 + sum(row_heights) + 16
        image = PILImage.new("RGB", (width, height), "#f4f6f9")
        draw = ImageDraw.Draw(image)
        y = 12
        for row_index, offset in enumerate(range(0, len(entries), 3)):
            row_entries = entries[offset:offset + 3]
            y = self._draw_team_gallery(image, draw, row_entries, y, padding, width, font, "#132033", "#d3dce7")
        image.save(output_path, format="PNG", optimize=True)
        return output_path

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
    def _draw_card_text(draw, text, x, y, font, fill, centered=False, bold=False):
        bbox = draw.textbbox((0, 0), text, font=font)
        if centered:
            x -= (bbox[2] - bbox[0]) / 2
        draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill,
                  stroke_width=1 if bold else 0, stroke_fill=fill)

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
    def _pick_result_symbol_font(self, size: int):
        from PIL import ImageFont
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/seguisym.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
        ):
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except OSError:
                    continue
        return self._pick_banner_font(size)

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

