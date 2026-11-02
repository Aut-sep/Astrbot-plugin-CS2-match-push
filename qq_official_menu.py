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
from .player_portraits import DISPLAY_WIDTH

OFFICIAL_PLATFORMS = {"qq_official", "qq_official_webhook"}


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
    """Build the QQ inline keyboard used by the fixed CS2 menus."""
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
        """Send a QQ Markdown card, retaining a readable fallback on rejection.

        Args:
            event: The triggering QQ official group or direct message event.
            text: Markdown card content, including an optional inline portrait.
            rows: Optional rows of button label and command pairs.
            player: Optional player snapshot with a cached portrait and source URL.
        """
        if event.get_platform_name() not in OFFICIAL_PLATFORMS:
            return
        import botpy.message
        from botpy.http import Route

        message = event.message_obj
        raw = message.raw_message
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
            await event.send(
                event.plain_result("CS 按钮菜单支持 QQ 官方机器人的群聊和单聊。")
            )
            event.stop_event()
            return
        payload = {
            "msg_type": 2,
            "markdown": {"content": text},
            "msg_id": message.message_id,
            "msg_seq": random.randint(1, 10000),
        }
        if rows:
            payload["keyboard"] = build_keyboard(rows)
        try:
            result = await event.bot.api._http.request(route, json=payload)
            if not isinstance(result, dict) or not result.get("id"):
                raise RuntimeError("QQ did not return a message ID")
        except Exception as exc:
            logger.warning("[CS] QQ card send failed (%s)", type(exc).__name__)
            # A source CDN may deny QQ's image fetcher. Keep Markdown formatting
            # in a text card and upload the bundled portrait separately instead.
            caption = re.sub(r"!\[[^\n]*\]\([^\n]*\)\n*", "", text)
            markdown_sent = False
            if player:
                retry = {**payload, "markdown": {"content": caption}}
                retry["msg_seq"] += 1
                try:
                    result = await event.bot.api._http.request(route, json=retry)
                    markdown_sent = isinstance(result, dict) and bool(result.get("id"))
                except Exception as retry_exc:
                    logger.warning(
                        "[CS] QQ text card send failed (%s)", type(retry_exc).__name__
                    )
            fallback = re.sub(r"(?m)^#{1,2} |^> |^\*\*\*$", "", caption).replace(
                "**", ""
            )
            if rows:
                fallback += "\n" + "\n".join(
                    f"{label}：{command}" for row in rows for label, command in row
                )
            components = [] if markdown_sent else [Plain(fallback)]
            if player and (self.root / player["image_file"]).is_file():
                components.append(
                    Image.fromFileSystem(str(self.root / player["image_file"]))
                )
            if components:
                try:
                    await event.send(event.chain_result(components).use_markdown(False))
                except Exception as image_exc:
                    if not player:
                        raise
                    logger.warning(
                        "[CS] QQ portrait fallback failed (%s)",
                        type(image_exc).__name__,
                    )
                    if not markdown_sent:
                        await event.send(
                            event.plain_result(fallback).use_markdown(False)
                        )
        else:
            # Without a configured public photo directory, send the local image
            # explicitly: QQ can accept Markdown while its image later fails.
            if player and "![" not in text:
                portrait = self.root / player["image_file"]
                if portrait.is_file():
                    try:
                        await event.send(
                            event.chain_result(
                                [Image.fromFileSystem(str(portrait))]
                            ).use_markdown(False)
                        )
                    except Exception as image_exc:
                        logger.warning(
                            "[CS] QQ local portrait send failed (%s)",
                            type(image_exc).__name__,
                        )
        event.stop_event()

    async def menu(self, event):
        await self.send(
            event,
            "# 🎮 CS2 菜单\n> 选择下面的功能。",
            [
                [
                    ("今日赛程", "/cs今日赛程"),
                    ("未来赛程", "/cs未来赛程"),
                ],
                [
                    ("指令帮助", "/cs帮助"),
                    ("今日选手", "/cs今日选手"),
                ],
                [("Major 对阵图", "/cs对阵图")],
            ],
        )

    async def help(self, event):
        await self.send(
            event,
            "# 📖 CS2 指令帮助\n> 点击即可使用。今日赛程按北京时间显示当天日报。",
            [
                [("今日赛程", "/cs今日赛程"), ("未来赛程", "/cs未来赛程")],
                [("全部赛程", "/cs比赛"), ("关注战队", "/cs关注列表")],
                [("今日选手", "/cs今日选手"), ("返回菜单", "/cs")],
                [("Major 对阵图", "/cs对阵图")],
            ],
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
                [[("返回菜单", "/cs")]],
            )
            return
        player = dict(result.player)
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
        details_line = " · ".join(details) + "\n\n" if details else ""
        compact = self.root / "player_cards" / f"{player['id']}_compact.png"
        if compact.is_file():
            player["image_file"] = str(compact.relative_to(self.root))
        portrait = self.root / player["image_file"]
        width, height = DISPLAY_WIDTH, DISPLAY_WIDTH
        photo = ""
        if portrait.is_file():
            with PillowImage.open(portrait) as image:
                height = max(1, round(width * image.height / image.width))
            if self.photo_base_url:
                version = hashlib.sha256(portrait.read_bytes()).hexdigest()[:12]
                photo_url = f"{self.photo_base_url}/{quote(portrait.name)}?v={version}"
                photo = f"![选手照片 #{width}px #{height}px]({photo_url})\n\n"
        reminder = "> 你今天已经抽取过啦，明天再来吧\n\n" if result.repeated else ""
        text = (
            "# 🌟 今日选手\n"
            f"{reminder}"
            f"> {result.day.replace('-', '.')} · 抽取成功\n\n"
            "🎉 **恭喜你！今天的专属选手是——**\n\n"
            f"## **【{player['name']}】**\n"
            f"**{player['team']}** · {player['role']}\n\n"
            f"{details_line}"
            f"{photo}"
        )
        await self.send(
            event,
            text,
            [[("返回 CS 菜单", "/cs"), ("抽取选手", "/cs今日选手")]],
            player=player,
        )
