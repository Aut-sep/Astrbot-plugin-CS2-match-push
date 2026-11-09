"""QQ official CS2 menus and daily player cards."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

from PIL import Image as PillowImage

from astrbot.api import logger
from astrbot.api.message_components import Image, Plain

from .daily_players import DailyPlayerDraw
from .official_extension import OFFICIAL_PLATFORMS, feature_enabled
from .player_portraits import DISPLAY_WIDTH

FEATURE_LABELS = {
    "help": "指令帮助",
    "schedule": "未来赛程",
    "bracket": "Major 对阵",
    "results": "赛后结果",
    "player": "今日选手",
    "followed": "关注战队",
    "status": "插件状态",
}

FEATURE_COMMANDS = {
    "/cs": "help",
    "/cs帮助": "help",
    "/cs菜单": "help",
    "/cs今日赛程": "schedule",
    "/cs未来赛程": "schedule",
    "/cs三日赛程": "schedule",
    "/cs比赛": "schedule",
    "/cs对阵图": "bracket",
    "/cs赛果": "results",
    "/cs今日选手": "player",
    "/cs关注列表": "followed",
    "/cs状态": "status",
}

FEATURE_ACTIONS = (
    ("schedule", "今日赛程", "/cs今日赛程"),
    ("schedule", "未来赛程", "/cs未来赛程"),
    ("bracket", "Major 对阵", "/cs对阵图"),
    ("bracket", "Major 下一阶段", "/cs对阵图 下一阶段"),
    ("results", "赛后结果", "/cs赛果"),
    ("player", "今日选手", "/cs今日选手"),
    ("followed", "关注战队", "/cs关注列表"),
    ("status", "插件状态", "/cs状态"),
    ("help", "指令帮助", "/cs帮助"),
)

FEATURE_TITLES = {
    "help": "CS2 功能菜单",
    "schedule": "CS2 赛程",
    "bracket": "CS2 Major 对阵",
    "results": "CS2 赛果",
    "player": "今日选手",
    "followed": "已关注战队",
    "status": "CS2 插件状态",
}

MAX_KEYBOARD_ROWS = 5
MAX_BUTTONS_PER_ROW = 2


def player_age(birth_date: str, day: str) -> int | None:
    """Calculate full years on the draw date, omitting incomplete data."""
    try:
        born = date.fromisoformat(birth_date)
        today = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    if born > today:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def build_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """Build a compact QQ inline keyboard, capped at five rows of two."""
    rows = [row[:MAX_BUTTONS_PER_ROW] for row in rows[:MAX_KEYBOARD_ROWS] if row]
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        {
                            "id": f"cs_{row_index}_{column}",
                            "render_data": {
                                "label": label,
                                "visited_label": label,
                                "style": 1,
                            },
                            "action": {
                                "type": 2,
                                "permission": {"type": 2},
                                "data": command,
                                "enter": True,
                                "unsupport_tips": f"请手动发送 {command}",
                            },
                        }
                        for column, (label, command) in enumerate(row)
                    ]
                }
                for row_index, row in enumerate(rows)
            ]
        }
    }


class OfficialCSMenu:
    def __init__(self, plugin):
        self.plugin = plugin
        self.root = Path(__file__).parent
        catalog = json.loads(
            (self.root / "qq_players.json").read_text(encoding="utf-8")
        )
        self.players_by_id = {player["id"]: player for player in catalog["players"]}
        self.draws = DailyPlayerDraw(
            Path(plugin._data_dir) / "qq_daily_players.sqlite3", catalog["players"]
        )
        self.photo_base_url = (
            str(getattr(plugin, "_qq_player_photo_base_url", "") or "")
            .strip()
            .rstrip("/")
        )
        parsed = urlsplit(self.photo_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            self.photo_base_url = ""

    async def send(self, event, text: str, rows=None, player=None):
        """Reply with text and optional buttons/photo using the current event."""
        if event.get_platform_name() not in OFFICIAL_PLATFORMS or not self._rich_enabled():
            content = (
                self._fallback_text(text, rows)
                if event.get_platform_name() in OFFICIAL_PLATFORMS
                else text
            )
            return await self._framework_reply(event, content, player=player)

        return await self._send_official_card(event, text, rows=rows, player=player)

    def _rich_enabled(self) -> bool:
        return bool(self.plugin.store.get("official_rich_replies", True))

    def _enabled(self, feature: str) -> bool:
        return feature_enabled(self.plugin, feature)

    @staticmethod
    def _plain_text(text: str) -> str:
        text = re.sub(r"!\[[^\n]*\]\([^\n]*\)\n*", "", str(text))
        text = re.sub(r"(?m)^#{1,3}\s*", "", text)
        text = re.sub(r"(?m)^>\s?", "", text)
        text = text.replace("**", "").replace("`", "")
        return text.strip()

    async def _framework_reply(self, event, text: str, *, components=None, player=None):
        """Readable passive framework reply used by OneBot and plain QQ mode."""
        content = [Plain(self._plain_text(text))]
        for component in components or []:
            if isinstance(component, Image):
                content.append(component)
        if player:
            portrait = self.root / player["image_file"]
            if portrait.is_file() and not any(isinstance(item, Image) for item in content):
                content.append(Image.fromFileSystem(str(portrait)))
        try:
            await event.send(event.chain_result(content).use_markdown(False))
        except Exception as exc:
            if len(content) == 1:
                raise
            logger.warning("[CS] Local reply image failed (%s)", type(exc).__name__)
            await event.send(event.plain_result(self._plain_text(text)).use_markdown(False))
        event.stop_event()

    @staticmethod
    def _summary_with_image(text: str) -> str:
        plain = OfficialCSMenu._plain_text(text)
        first_lines = "\n".join(plain.splitlines()[:2]).strip()
        return f"{first_lines}\n图片已附在回复中。" if first_lines else "详细内容见下方图片。"

    async def _send_official_card(self, event, text: str, *, rows=None, player=None):
        """Send a passive QQ rich reply, with a framework fallback on failure."""
        try:
            import botpy.message
            from botpy.http import Route
        except Exception as exc:
            logger.warning("[CS] QQ rich replies unavailable (%s)", type(exc).__name__)
            await self._framework_reply(
                event, self._fallback_text(text, rows), player=player
            )
            return

        message = event.message_obj
        raw = getattr(message, "raw_message", None)
        if isinstance(raw, botpy.message.GroupMessage):
            route = Route(
                "POST",
                "/v2/groups/{group_openid}/messages",
                group_openid=raw.group_openid,
            )
        elif isinstance(raw, botpy.message.C2CMessage):
            route = Route(
                "POST", "/v2/users/{openid}/messages", openid=raw.author.user_openid
            )
        else:
            return await self._framework_reply(
                event, self._fallback_text(text, rows), player=player
            )
        payload = {
            "msg_type": 2,
            "markdown": {"content": text},
            "msg_id": message.message_id,
            "msg_seq": random.randint(1, 10000),
        }
        if rows:
            payload["keyboard"] = build_keyboard(self._filter_rows(rows))
        try:
            result = await event.bot.api._http.request(route, json=payload)
            if not isinstance(result, dict) or not result.get("id"):
                raise RuntimeError("QQ did not return a message ID")
        except Exception as exc:
            logger.warning(
                "[CS] QQ card send failed (%s); falling back to plain", type(exc).__name__
            )
            return await self._framework_reply(
                event, self._fallback_text(text, rows), player=player
            )
        event.stop_event()

    def _fallback_text(self, text: str, rows=None) -> str:
        lines = [self._plain_text(text)]
        actions = [action for row in self._filter_rows(rows) for action in row]
        if len(actions) == 1:
            label, command = actions[0]
            lines.append(f"{label}（发送 {command}）")
        elif actions:
            lines.extend(["", "可直接发送："])
            lines.extend(f"{label}：{command}" for label, command in actions)
        return "\n".join(lines)

    def _filter_rows(self, rows):
        filtered = []
        for row in rows or []:
            current = [
                (label, command)
                for label, command in row
                if self._enabled(FEATURE_COMMANDS.get(command.split()[0], "help"))
            ]
            if current:
                filtered.extend(
                    current[index:index + MAX_BUTTONS_PER_ROW]
                    for index in range(0, len(current), MAX_BUTTONS_PER_ROW)
                )
        return filtered[:MAX_KEYBOARD_ROWS]

    async def reply(self, event, text: str, *, feature: str, components=None):
        """Return the requested result once; menus are explicit user actions."""
        images = [item for item in components or [] if isinstance(item, Image)]
        if images:
            content = [Plain(self._summary_with_image(text)), *images]
            await event.send(event.chain_result(content).use_markdown(False))
            event.stop_event()
            return
        if event.get_platform_name() not in OFFICIAL_PLATFORMS or not self._rich_enabled():
            return await self._framework_reply(event, text, components=images)

        body = self._plain_text(text)
        title = FEATURE_TITLES.get(feature, "CS2 查询结果")
        card = f"# {title}\n\n{body}" if body else f"# {title}"
        await self._send_official_card(event, card)

    async def _send_menu(self, event, *, title: str, text: str):
        actions = [item for item in FEATURE_ACTIONS if self._enabled(item[0])]
        rows = [actions[index:index + MAX_BUTTONS_PER_ROW]
                for index in range(0, len(actions), MAX_BUTTONS_PER_ROW)]
        pairs = [tuple((label, command) for _, label, command in row) for row in rows]
        return await self.send(event, f"# {title}\n\n{text}", pairs)

    async def menu(self, event):
        await self._send_menu(
            event,
            title="CS2 功能菜单",
            text="选择要查看的功能。菜单仅显示当前已启用的官方功能。",
        )

    async def help(self, event):
        actions = [action for action in FEATURE_ACTIONS if self._enabled(action[0])]
        if self._enabled("help"):
            actions.append(("help", "返回菜单", "/cs"))
        rows = [actions[index:index + MAX_BUTTONS_PER_ROW]
                for index in range(0, len(actions), MAX_BUTTONS_PER_ROW)]
        buttons = [
            [(label, command) for _, label, command in row]
            for row in rows
        ]
        await self.send(
            event,
            "# CS2 指令帮助\n\n"
            "按钮对应各项查询命令。赛程和赛果按北京时间显示；"
            "选手每日可查看原抽取结果，不会重复抽取。\n\n"
            "配置建议由管理员优先在 AstrBot 的插件面板中完成（插件详情 → CS2 管理）。",
            buttons,
        )

    async def schedule(self, event, days=1):
        """Use the same read-only schedule query as all chat adapters."""
        return await self.plugin._schedule_query_result(event, days)

    async def player(self, event):
        sender = str(event.get_sender_id() or "").strip()
        if not sender:
            await self.send(event, "无法识别你的账号，暂时不能抽取今日选手。")
            return
        # Exclude the group from the identity so moving between groups does not
        # permit a reroll. Keep raw openids out of the persistent allocation file.
        user_key = hashlib.sha256(
            f"{event.get_platform_id()}:{sender}".encode()
        ).hexdigest()
        try:
            result = await asyncio.to_thread(self.draws.draw, user_key)
        except Exception as exc:
            logger.warning("[CS] Daily player storage failed (%s)", type(exc).__name__)
            await self.send(event, "今日选手暂时无法保存，请稍后重试。")
            return
        if result.player is None:
            await self.send(
                event,
                "# 🌟 今日选手\n今天的选手已全部被抽取，请明天再来。",
            )
            return
        player = dict(result.player)
        official = event.get_platform_name() in OFFICIAL_PLATFORMS
        if official and result.repeated:
            return await self._framework_reply(
                event, f"你今天已抽到 {player['name']}（{player['team']}），明天再来吧。"
            )
        # Older saved draws lack nationality or birthday. Enrich only the display,
        # keeping the original draw, team, role and persistent snapshot intact.
        profile = self.players_by_id.get(player["id"], {})
        nationality = player.get("nationality") or profile.get("nationality", "")
        country_code = player.get("country_code") or profile.get("country_code", "")
        flag = ""
        if re.fullmatch(r"[A-Z]{2}", country_code):
            flag = "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in country_code)
        age = player_age(player.get("birth_date") or profile.get("birth_date"), result.day)
        details = [f"{flag} {nationality}".strip()] if nationality else []
        if age is not None:
            details.append(f"{age}岁")
        profile_line = " · ".join(details)
        compact = self.root / "player_cards" / f"{player['id']}_compact.png"
        if compact.is_file():
            player["image_file"] = str(compact.relative_to(self.root))
        portrait = self.root / player["image_file"]
        rows = [[("抽取今日选手", "/cs今日选手")]] if official else None
        photo = ""
        if official and self._rich_enabled() and self.photo_base_url and portrait.is_file():
            width = DISPLAY_WIDTH
            try:
                with PillowImage.open(portrait) as image:
                    height = max(1, round(width * image.height / image.width))
                version = hashlib.sha256(portrait.read_bytes()).hexdigest()[:12]
                photo_url = f"{self.photo_base_url}/{quote(portrait.name)}?v={version}"
                photo = f"![选手照片 #{width}px #{height}px]({photo_url})\n\n"
            except OSError as exc:
                logger.warning("[CS] Could not prepare QQ portrait (%s)", type(exc).__name__)
        if official and not photo:
            player_info = " · ".join([player["team"], player["role"], *details])
            text = f"🌟 今日选手 · {player['name']}\n{player_info}"
            return await self._framework_reply(
                event, self._fallback_text(text, rows), player=player
            )
        reminder = "> 你今天已经抽取过啦，明天再来吧\n\n" if result.repeated else ""
        details_line = f"{profile_line}\n\n" if profile_line else ""
        text = (
            "# 🌟 今日选手\n\n"
            f"{reminder}"
            "🎉 **恭喜你！今天的专属选手是——**\n\n"
            f"## **【{player['name']}】**\n\n"
            f"**{player['team']}** · {player['role']}\n\n"
            f"{details_line}"
            f"{photo}"
        )
        await self.send(event, text, rows=rows, player=player)
