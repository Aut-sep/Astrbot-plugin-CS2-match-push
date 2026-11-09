"""Verify result-only official replies without contacting QQ."""
import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image as PillowImage

import test_swiss_layout as fixture

# Only the message components supplied by AstrBot are substituted.
import sys
components=types.ModuleType('astrbot.api.message_components')
class Image:
    @classmethod
    def fromFileSystem(cls,path):return cls()
components.Image=Image
components.Plain=str
previous_components=sys.modules.get('astrbot.api.message_components')
sys.modules['astrbot.api.message_components']=components
menu_module=importlib.import_module('cs_swiss_checked.qq_official_menu')
if previous_components is None:
    sys.modules.pop('astrbot.api.message_components',None)
else:
    sys.modules['astrbot.api.message_components']=previous_components


class OfficialResultTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.menu = menu_module.OfficialCSMenu.__new__(menu_module.OfficialCSMenu)
        self.settings = {
            'official_extension_enabled': True,
            'official_features': ['help', 'bracket', 'player'],
            'official_rich_replies': True,
        }
        self.menu.plugin = types.SimpleNamespace(store=self.settings)
        botpy = types.ModuleType('botpy')
        message = types.ModuleType('botpy.message')
        http = types.ModuleType('botpy.http')
        botpy.message = message
        message.GroupMessage = type('GroupMessage', (types.SimpleNamespace,), {})
        message.C2CMessage = type('C2CMessage', (types.SimpleNamespace,), {})
        http.Route = lambda method, path, **params: (method, path.format(**params))
        modules = patch.dict(sys.modules, {'botpy': botpy, 'botpy.message': message, 'botpy.http': http})
        modules.start()
        self.addCleanup(modules.stop)
        self.request = AsyncMock(return_value={'id': 'offline-reply'})
        self.event = types.SimpleNamespace(
            get_platform_name=lambda: 'qq_official',
            get_sender_id=lambda: 'offline-user',
            get_platform_id=lambda: 'offline-account',
            message_obj=types.SimpleNamespace(
                raw_message=message.GroupMessage(group_openid='offline-group'),
                message_id='offline-message',
            ),
            bot=types.SimpleNamespace(api=types.SimpleNamespace(_http=types.SimpleNamespace(request=self.request))),
            send=AsyncMock(),
            stop_event=Mock(),
            chain_result=lambda chain: types.SimpleNamespace(use_markdown=lambda value: chain),
        )

    def prepare_player(self, *, public=False, repeated=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.menu.root = Path(directory.name)
        player = {
            'id': 'demo', 'name': 'Virtual Player', 'team': 'Demo Team',
            'role': '步枪手', 'image_file': 'demo.png',
        }
        self.menu.players_by_id = {'demo': player}
        self.menu.draws = types.SimpleNamespace(draw=lambda key: types.SimpleNamespace(
            player=player, day='2026-11-03', repeated=repeated,
        ))
        self.menu.photo_base_url = 'https://example.test/portraits' if public else ''
        PillowImage.new('RGB', (30, 20)).save(self.menu.root / player['image_file'])

    async def test_text_query_result_has_no_keyboard(self):
        await self.menu.reply(self.event, '当前无进行中的阶段', feature='bracket')
        self.request.assert_awaited_once()
        payload = self.request.await_args.kwargs['json']
        self.assertIn('当前无进行中的阶段', payload['markdown']['content'])
        self.assertNotIn('keyboard', payload)
        self.event.send.assert_not_awaited()

    async def test_unsupported_rich_source_falls_back_to_query_result(self):
        self.event.message_obj.raw_message = object()
        await self.menu.reply(self.event, '当前无进行中的阶段', feature='bracket')
        self.request.assert_not_awaited()
        self.event.send.assert_awaited_once_with(['CS2 Major 对阵\n\n当前无进行中的阶段'])
        self.event.stop_event.assert_called_once()

    async def test_image_result_is_one_message_without_extra_card(self):
        image=Image()
        await self.menu.reply(self.event,'完整对阵说明',feature='bracket',components=[image])
        self.event.send.assert_awaited_once_with([image])
        self.request.assert_not_awaited()
        self.event.stop_event.assert_called_once()

    async def test_explicit_menu_still_has_buttons(self):
        await self.menu.menu(self.event)
        self.request.assert_awaited_once()
        rows = self.request.await_args.kwargs['json']['keyboard']['content']['rows']
        self.assertEqual(
            [button['action']['data'] for button in rows[0]['buttons']],
            ['/major对阵图', '/major对阵图 下一阶段'],
        )
        self.event.send.assert_not_awaited()

    async def test_public_portrait_and_draw_button_share_one_card(self):
        self.prepare_player(public=True)
        await self.menu.player(self.event)
        self.request.assert_awaited_once()
        payload = self.request.await_args.kwargs['json']
        text = payload['markdown']['content']
        self.assertIn('恭喜你！今天的专属选手是——', text)
        self.assertIn('**【Virtual Player】**', text)
        self.assertIn('https://example.test/portraits/demo.png?v=', text)
        self.assertNotIn('2026.11.03', text)
        action = payload['keyboard']['content']['rows'][0]['buttons'][0]['action']
        self.assertEqual(action['permission'], {'type': 2})
        self.assertEqual(action['data'], '/cs今日选手')
        self.event.send.assert_not_awaited()
        self.event.stop_event.assert_called_once()

    async def test_local_portrait_and_command_share_one_reply(self):
        self.prepare_player()
        await self.menu.player(self.event)
        self.request.assert_not_awaited()
        self.assert_plain_portrait_reply()

    async def test_rejected_public_card_falls_back_to_one_local_reply(self):
        self.prepare_player(public=True)
        self.request.side_effect = RuntimeError('simulated rejection')
        await self.menu.player(self.event)
        self.request.assert_awaited_once()
        self.assert_plain_portrait_reply()

    def assert_plain_portrait_reply(self):
        self.event.send.assert_awaited_once()
        text, image = self.event.send.await_args.args[0]
        self.assertIn('Virtual Player', text)
        self.assertIn('/cs今日选手', text)
        self.assertIsInstance(image, Image)
        self.event.stop_event.assert_called_once()

    async def test_repeated_official_draw_is_one_short_reply(self):
        self.prepare_player(public=True, repeated=True)
        self.event.get_platform_name = lambda: 'qq_official_webhook'
        await self.menu.player(self.event)
        self.request.assert_not_awaited()
        self.event.send.assert_awaited_once_with(['你今天已抽到 Virtual Player（Demo Team），明天再来吧。'])
        self.event.stop_event.assert_called_once()

    async def test_repeated_onebot_draw_keeps_profile_and_portrait(self):
        self.prepare_player(public=True, repeated=True)
        self.event.get_platform_name = lambda: 'aiocqhttp'
        await self.menu.player(self.event)
        self.request.assert_not_awaited()
        self.event.send.assert_awaited_once()
        text, image = self.event.send.await_args.args[0]
        self.assertIn('你今天已经抽取过啦，明天再来吧', text)
        self.assertIn('【Virtual Player】', text)
        self.assertNotIn('/cs今日选手', text)
        self.assertIsInstance(image, Image)
