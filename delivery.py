"""主动消息发送：兼容旧版工具接口与当前 AstrBot Context 接口。"""

import inspect
import random
from types import SimpleNamespace

from astrbot.api.star import StarTools

from .config import message_target_id


class DeliveryError(RuntimeError):
    """目标未发送成功，调用方应保留通知状态并稍后重试。"""


async def _get_platforms(context):
    """兼容提供发现方法的版本，以及当前官方 PlatformManager。"""
    discover = getattr(context, "get_all_platforms", None)
    if callable(discover):
        platforms = discover()
    else:
        manager = getattr(context, "platform_manager", None)
        discover = getattr(manager, "get_insts", None)
        if callable(discover):
            platforms = discover()
        else:
            platforms = getattr(manager, "platform_insts", None)
    if inspect.isawaitable(platforms):
        platforms = await platforms
    if platforms is None:
        raise DeliveryError("无法发现 AstrBot 消息平台，请检查平台适配器配置")
    return platforms


async def send_target(
    context, target_type: str, target_id: str, chain, *, platform_id: str | None = None
):
    """发送到一个 QQ 群或私聊目标，原样返回结果；明确失败时抛出异常。

    默认保留 aiocqhttp 自动选择；显式 platform_id 可选 QQ 官方适配器。
    官方接口仅在返回消息 ID 后算成功，失败绝不切换到另一个机器人。
    """
    if target_type not in {"group", "private"}:
        raise ValueError("消息目标类型必须是 group 或 private")
    target_id = message_target_id(target_id)
    message_type = "FriendMessage" if target_type == "private" else "GroupMessage"

    legacy_send = getattr(StarTools, "send_message_by_id", None)
    if platform_id is None and callable(legacy_send):
        if not target_id.isdigit():
            raise DeliveryError("官方 OpenID 必须指定 QQ 官方平台 ID")
        result = await legacy_send(
            type=message_type,
            id=target_id,
            message_chain=chain,
            platform="aiocqhttp",
        )
    else:
        platforms = await _get_platforms(context)
        candidates = []
        for platform in platforms:
            meta = platform.meta()
            if (platform_id and meta.id == platform_id) or (
                not platform_id and meta.name == "aiocqhttp"
            ):
                candidates.append(platform)
        if not candidates:
            detail = f"（ID: {platform_id}）" if platform_id else ""
            raise DeliveryError(f"未找到目标 QQ 平台实例{detail}")
        if len(candidates) != 1:
            raise DeliveryError("发现多个 QQ 平台实例，请显式指定 platform_id 后再发送")
        selected = candidates[0]
        meta = selected.meta()
        if meta.name in {"qq_official", "qq_official_webhook"}:
            return await _send_official(selected, target_type, target_id, chain)
        if meta.name != "aiocqhttp":
            raise DeliveryError("比赛推送仅支持 QQ 官方或 OneBot 适配器")
        if not target_id.isdigit():
            raise DeliveryError("普通 QQ 平台需要数字 QQ 号或群号，不能使用官方 OpenID")
        instance_id = meta.id
        if not isinstance(instance_id, str) or not instance_id or ":" in instance_id:
            raise DeliveryError("aiocqhttp 平台实例缺少有效 ID")
        send_message = getattr(context, "send_message", None)
        if not callable(send_message):
            raise DeliveryError("当前 AstrBot Context 不支持主动发送消息")
        result = await send_message(f"{instance_id}:{message_type}:{target_id}", chain)
    if result is False:
        raise DeliveryError("AstrBot 未发送消息：发送接口返回 False")
    return result


async def _send_official(platform, target_type: str, target_id: str, chain):
    """Send a CS notification through the explicitly selected official bot.

    Args:
        platform: Running QQ official adapter instance.
        target_type: Group or private scene.
        target_id: Scene-specific OpenID, not a numeric QQ group number.
        chain: Notification containing text and at most one image.

    Returns:
        The API response including the sent message ID.

    Raises:
        DeliveryError: Target, content, client or API receipt is invalid.
    """
    if target_id.isdigit() and len(target_id) < 16:
        raise DeliveryError(
            "QQ 官方推送需要群 OpenID；请在接收群 @官方机器人发送 /cs设置群"
        )
    components = getattr(chain, "chain", [])
    if (
        any(item.type not in {"Plain", "Image"} for item in components)
        or sum(item.type == "Image" for item in components) > 1
    ):
        raise DeliveryError("比赛通知仅支持文字和单张图片")
    client = getattr(platform, "client", None)
    if client is None:
        raise DeliveryError("QQ 官方平台客户端尚未就绪")
    from botpy.http import Route

    from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import (
        QQOfficialMessageEvent,
    )

    text, image, *_ = await QQOfficialMessageEvent._parse_to_qqofficial(chain)
    if not text and not image:
        raise DeliveryError("比赛通知内容为空")
    key = "group_openid" if target_type == "group" else "openid"
    payload = {"content": text, "msg_type": 0, "msg_seq": random.randint(1, 10000)}
    if image:
        payload["media"] = await QQOfficialMessageEvent.upload_group_and_c2c_image(
            SimpleNamespace(bot=client),
            image,
            1,
            **{key: target_id},
        )
        payload["msg_type"] = 7
    path = (
        "/v2/groups/{group_openid}/messages"
        if target_type == "group"
        else "/v2/users/{openid}/messages"
    )
    result = await client.api._http.request(
        Route("POST", path, **{key: target_id}), json=payload
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise DeliveryError("QQ 官方接口未返回消息 ID，本次推送未确认成功")
    return result
