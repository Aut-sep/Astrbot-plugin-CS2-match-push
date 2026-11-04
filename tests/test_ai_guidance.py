"""Offline query guidance checks; no model/API requests or QQ messages."""

import importlib
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_group_rules as fixtures

main = fixtures.main
guidance = importlib.import_module("cs_start_checked.ai_guidance")
TOOLS = list(guidance.FEATURES)


class TextPart:
    def __init__(self, text):
        self.text = text
        self.temporary = False

    def mark_as_temp(self):
        self.temporary = True
        return self


class IntentTests(unittest.TestCase):
    def test_real_question_and_team_aliases(self):
        for message in (
            "塔菲最近有navi比赛吗",
            "今晚绿龙打谁",
            "CS2 明天的赛程",
            "Natus Vincere 几点开赛",
        ):
            self.assertIn("get_cs2_schedule", guidance.query_tools(message), message)

    def test_result_major_and_followed_are_separate(self):
        self.assertEqual(
            guidance.query_tools("昨天 NAVI 赢了吗"), ["get_cs2_match_result"]
        )
        self.assertEqual(
            guidance.query_tools("Major 下一阶段对阵"), ["get_cs2_major_bracket"]
        )
        self.assertEqual(
            guidance.query_tools("本群 CS2 关注了哪些战队"), ["get_cs2_followed_teams"]
        )
        self.assertEqual(len(guidance.query_tools("NAVI 昨天比分和明天赛程")), 2)

    def test_other_games_news_and_substrings_do_not_trigger(self):
        for message in (
            "欧冠明天赛程",
            "NAVI Dota2 最近有比赛吗",
            "NAVI 最近有转会新闻吗",
            "最近有 NAVI 比赛新闻吗",
            "CS2 转会分析",
            "navigation 比赛时间",
            "明天有什么比赛",
            "我们群关注了谁",
            "x" * 4097,
        ):
            self.assertEqual(guidance.query_tools(message), [], message)

    def test_explicit_web_source_is_respected(self):
        for message in (
            "只用网页搜索 NAVI 赛程",
            "不要用插件查 NAVI 比赛",
            "使用 Tavily 查 CS2 赛程",
            "Use Tavily for NAVI 赛程",
            "不要调用任何工具，告诉我 NAVI 赛程",
        ):
            self.assertEqual(guidance.query_tools(message), [], message)


class GuidanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.GroupRuleTests.setUp(self)
        self.event = types.SimpleNamespace(
            get_message_str=lambda: "最近有navi比赛吗",
            get_group_id=lambda: fixtures.A,
            get_platform_name=lambda: "aiocqhttp",
            get_platform_id=lambda: "offline",
        )
        self.req = types.SimpleNamespace(
            prompt="original user prompt",
            system_prompt="original persona",
            contexts=[{"role": "user", "content": "unrelated history"}],
            extra_user_content_parts=[],
            func_tool=types.SimpleNamespace(
                tools=[
                    types.SimpleNamespace(name=name, active=True)
                    for name in [*TOOLS, "web_search_tavily"]
                ]
            ),
        )
        self.host = patch.dict(
            sys.modules,
            {"astrbot.core.agent.message": types.SimpleNamespace(TextPart=TextPart)},
        )
        self.host.start()
        self.addCleanup(self.host.stop)

    async def test_hook_adds_one_temporary_hint_without_mutating_persona_or_tools(self):
        tools = self.req.func_tool
        await self.p.guide_cs2_ai_query(self.event, self.req)
        await self.p.guide_cs2_ai_query(self.event, self.req)
        self.assertEqual(len(self.req.extra_user_content_parts), 1)
        part = self.req.extra_user_content_parts[0]
        self.assertTrue(part.temporary)
        self.assertIn("get_cs2_schedule", part.text)
        self.assertIn("3个日历天", part.text)
        self.assertIn("Asia/Shanghai", part.text)
        self.assertEqual(self.req.system_prompt, "original persona")
        self.assertEqual(self.req.prompt, "original user prompt")
        self.assertEqual(
            self.req.contexts, [{"role": "user", "content": "unrelated history"}]
        )
        self.assertIs(self.req.func_tool, tools)
        self.assertEqual(len(tools.tools), 5)

    async def test_disabled_or_unoffered_tool_is_never_added(self):
        self.req.func_tool.tools[0].active = False
        guidance.apply_guidance(self.p, self.event, self.req)
        self.assertEqual(self.req.extra_user_content_parts, [])
        self.req.func_tool = None
        guidance.apply_guidance(self.p, self.event, self.req)
        self.assertEqual(self.req.extra_user_content_parts, [])

    async def test_switch_can_disable_guidance(self):
        self.p._load_config({"ai_query_guidance_enabled": False})
        guidance.apply_guidance(self.p, self.event, self.req)
        self.assertEqual(self.req.extra_user_content_parts, [])

    async def test_official_permissions_and_token_are_respected(self):
        self.event.get_platform_name = lambda: "qq_official"
        guidance.apply_guidance(self.p, self.event, self.req)
        self.assertEqual(self.req.extra_user_content_parts, [])
        self.event.get_platform_name = lambda: "aiocqhttp"
        self.p._token = ""
        guidance.apply_guidance(self.p, self.event, self.req)
        self.assertEqual(self.req.extra_user_content_parts, [])
        self.event.get_message_str = lambda: "本群 CS2 关注了哪些战队"
        guidance.apply_guidance(self.p, self.event, self.req)
        self.assertIn(
            "get_cs2_followed_teams", self.req.extra_user_content_parts[0].text
        )

    async def test_unrelated_current_message_does_not_use_cs2_history(self):
        self.req.contexts = [{"role": "user", "content": "NAVI 明天赛程"}]
        self.event.get_message_str = lambda: "明天上海天气怎么样"
        guidance.apply_guidance(self.p, self.event, self.req)
        self.assertEqual(self.req.extra_user_content_parts, [])

    async def test_schedule_entrypoint_defaults_to_seven_days(self):
        self.p.client = types.SimpleNamespace(
            get_matches_between=AsyncMock(return_value=[])
        )
        result = json.loads(await self.p.ai_cs2_schedule(self.event, team="NAVI"))
        self.assertEqual(result["status"], "ok")
        start, end = self.p.client.get_matches_between.call_args.args
        self.assertEqual((end - start).days, 3)
