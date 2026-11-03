"""Verify result-only official replies without contacting QQ."""
import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock

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
        self.menu=menu_module.OfficialCSMenu.__new__(menu_module.OfficialCSMenu)
        settings={'official_extension_enabled':True,'official_features':['help','bracket','player'],
                  'official_rich_replies':True}
        self.menu.plugin=types.SimpleNamespace(store=types.SimpleNamespace(get=lambda key,default=None:settings.get(key,default)))
        self.menu._send_official_card=AsyncMock()
        self.event=types.SimpleNamespace(get_platform_name=lambda:'qq_official',send=AsyncMock(),
            stop_event=Mock(),chain_result=lambda chain:types.SimpleNamespace(chain=chain,use_markdown=lambda value:chain))

    async def test_text_query_result_has_no_keyboard(self):
        await self.menu.reply(self.event,'当前无进行中的阶段',feature='bracket')
        self.menu._send_official_card.assert_awaited_once()
        self.assertNotIn('rows',self.menu._send_official_card.await_args.kwargs)

    async def test_image_result_is_one_message_without_extra_card(self):
        image=Image()
        await self.menu.reply(self.event,'完整对阵说明',feature='bracket',components=[image])
        self.event.send.assert_awaited_once_with(['完整对阵说明\n图片已附在回复中。',image])
        self.menu._send_official_card.assert_not_awaited()
        self.event.stop_event.assert_called_once()

    async def test_explicit_menu_still_has_buttons(self):
        self.menu.send=AsyncMock()
        await self.menu.menu(self.event)
        self.assertTrue(self.menu.send.await_args.args[2])

    async def test_daily_player_result_does_not_attach_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            self.menu.root=Path(directory)
            self.menu.players_by_id={'demo':{'id':'demo','name':'Virtual Player'}}
            player={'id':'demo','name':'Virtual Player','team':'Demo Team','role':'步枪手','image_file':'missing.png'}
            self.menu.draws=types.SimpleNamespace(draw=lambda key:types.SimpleNamespace(player=player,day='2026-11-03',repeated=False))
            self.menu.photo_base_url=''
            self.menu.send=AsyncMock()
            self.event.get_sender_id=lambda:'offline-user'
            self.event.get_platform_id=lambda:'offline-account'
            await self.menu.player(self.event)
            self.assertEqual(len(self.menu.send.await_args.args),2)
            self.assertNotIn('rows',self.menu.send.await_args.kwargs)
