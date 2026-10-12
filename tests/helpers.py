"""只模拟 AstrBot 接口；业务代码、aiohttp 和持久化使用真实实现。"""

import importlib
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


def _decorator(*args, **kwargs):
    return lambda fn: fn


def load_module(name):
    if "cs2_plugin" not in sys.modules:
        root = Path(__file__).resolve().parents[1]
        package = ModuleType("cs2_plugin")
        package.__path__ = [str(root)]
        sys.modules["cs2_plugin"] = package
        astrbot = ModuleType("astrbot")
        api = ModuleType("astrbot.api")
        api.logger = logging.getLogger("cs2_test")
        star = ModuleType("astrbot.api.star")
        star.Context = object
        star.Star = type("Star", (), {"__init__": lambda self, *args: None})
        star.StarTools = SimpleNamespace()
        star.register = _decorator
        event = ModuleType("astrbot.api.event")
        event.AstrMessageEvent = object
        event.MessageEventResult = object
        event.filter = SimpleNamespace(
            command=_decorator, permission_type=_decorator, event_message_type=_decorator,
            PermissionType=SimpleNamespace(ADMIN="admin"),
            EventMessageType=SimpleNamespace(PRIVATE_MESSAGE="private"),
        )
        astrbot.api = api
        sys.modules.update({"astrbot": astrbot, "astrbot.api": api,
                            "astrbot.api.star": star, "astrbot.api.event": event})
    return importlib.import_module(f"cs2_plugin.{name}")
