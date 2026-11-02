"""主动消息发送：兼容旧版工具接口与当前 AstrBot Context 接口。"""

import inspect

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

    仅选择 aiocqhttp / OneBot 平台。官方扩展仅回复触发事件，不承接主动推送。
    发送失败绝不切换到另一个机器人。
    """
    if target_type not in {"group", "private"}:
        raise ValueError("消息目标类型必须是 group 或 private")
    target_id = message_target_id(target_id)
    message_type = "FriendMessage" if target_type == "private" else "GroupMessage"

    legacy_send = getattr(StarTools, "send_message_by_id", None)
    if platform_id is None and callable(legacy_send):
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
        if meta.name != "aiocqhttp":
            raise DeliveryError("主动推送仅支持普通 OneBot 机器人；官方端仅提供被动查询")
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
