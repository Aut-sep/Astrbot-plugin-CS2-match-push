import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.helpers import load_module


delivery = load_module("delivery")


def platform(instance_id="qq-bot-1", name="aiocqhttp"):
    return SimpleNamespace(meta=lambda: SimpleNamespace(id=instance_id, name=name))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chain = object()
        self.context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[platform()]),
            send_message=AsyncMock(return_value=True),
        )
        self.legacy_patch = patch.object(delivery.StarTools, "send_message_by_id", None, create=True)
        self.legacy_patch.start()
        self.addCleanup(self.legacy_patch.stop)

    async def test_current_context_sends_using_actual_instance_id(self):
        self.assertIs(await delivery.send_target(self.context, "group", "123", self.chain), True)
        self.context.send_message.assert_awaited_once_with("qq-bot-1:GroupMessage:123", self.chain)

    async def test_private_target_uses_friend_message(self):
        await delivery.send_target(self.context, "private", "456", self.chain)
        self.context.send_message.assert_awaited_once_with("qq-bot-1:FriendMessage:456", self.chain)

    async def test_get_all_platforms_compatibility(self):
        self.context.get_all_platforms = Mock(return_value=[platform("old-bot")])
        await delivery.send_target(self.context, "group", "123", self.chain)
        self.context.send_message.assert_awaited_once_with("old-bot:GroupMessage:123", self.chain)

    async def test_platform_manager_get_insts_is_supported(self):
        self.context.platform_manager = SimpleNamespace(get_insts=lambda: [platform("manager-bot")])
        await delivery.send_target(self.context, "group", "123", self.chain)
        self.context.send_message.assert_awaited_once_with("manager-bot:GroupMessage:123", self.chain)

    async def test_legacy_sender_is_preferred_and_result_is_preserved(self):
        result = {"message_id": "987"}
        legacy = AsyncMock(return_value=result)
        with patch.object(delivery.StarTools, "send_message_by_id", legacy):
            self.assertIs(await delivery.send_target(self.context, "group", "123", self.chain), result)
        legacy.assert_awaited_once_with(
            type="GroupMessage", id="123", message_chain=self.chain, platform="aiocqhttp",
        )
        self.context.send_message.assert_not_awaited()

    async def test_none_legacy_result_remains_compatible(self):
        with patch.object(delivery.StarTools, "send_message_by_id", AsyncMock(return_value=None)):
            self.assertIsNone(await delivery.send_target(self.context, "private", "123", self.chain))

    async def test_legacy_false_is_failure_and_does_not_fall_back(self):
        with patch.object(delivery.StarTools, "send_message_by_id", AsyncMock(return_value=False)):
            with self.assertRaises(delivery.DeliveryError):
                await delivery.send_target(self.context, "group", "123", self.chain)
        self.context.send_message.assert_not_awaited()

    async def test_context_false_is_failure(self):
        self.context.send_message.return_value = False
        with self.assertRaises(delivery.DeliveryError):
            await delivery.send_target(self.context, "group", "123", self.chain)
        self.context.send_message.assert_awaited_once()

    async def test_send_exception_is_propagated_without_second_attempt(self):
        error = RuntimeError("OneBot disconnected")
        legacy = AsyncMock(side_effect=error)
        with patch.object(delivery.StarTools, "send_message_by_id", legacy):
            with self.assertRaisesRegex(RuntimeError, "OneBot disconnected"):
                await delivery.send_target(self.context, "group", "123", self.chain)
        legacy.assert_awaited_once()
        self.context.send_message.assert_not_awaited()

    async def test_cancellation_propagates(self):
        self.context.send_message.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await delivery.send_target(self.context, "group", "123", self.chain)

    async def test_other_platforms_are_not_selected(self):
        self.context.platform_manager.platform_insts = [platform("telegram-bot", "telegram")]
        with self.assertRaisesRegex(delivery.DeliveryError, "未找到 aiocqhttp"):
            await delivery.send_target(self.context, "group", "123", self.chain)
        self.context.send_message.assert_not_awaited()

    async def test_ambiguous_bots_are_not_broadcast_or_chosen_arbitrarily(self):
        self.context.platform_manager.platform_insts.append(platform("qq-bot-2"))
        with self.assertRaisesRegex(delivery.DeliveryError, "多个 aiocqhttp"):
            await delivery.send_target(self.context, "group", "123", self.chain)
        self.context.send_message.assert_not_awaited()

    async def test_explicit_instance_routes_only_to_selected_bot(self):
        self.context.platform_manager.platform_insts.append(platform("qq-bot-2"))
        legacy = AsyncMock()
        with patch.object(delivery.StarTools, "send_message_by_id", legacy):
            await delivery.send_target(self.context, "group", "123", self.chain, platform_id="qq-bot-2")
        self.context.send_message.assert_awaited_once_with("qq-bot-2:GroupMessage:123", self.chain)
        legacy.assert_not_awaited()

    async def test_explicit_missing_instance_does_not_use_another_bot(self):
        with self.assertRaisesRegex(delivery.DeliveryError, "未找到 aiocqhttp"):
            await delivery.send_target(self.context, "group", "123", self.chain, platform_id="missing")
        self.context.send_message.assert_not_awaited()

    async def test_missing_discovery_or_platform_id_is_failure(self):
        for context in (
            SimpleNamespace(send_message=self.context.send_message),
            SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[platform(None)]),
                            send_message=self.context.send_message),
        ):
            with self.assertRaises(delivery.DeliveryError):
                await delivery.send_target(context, "group", "123", self.chain)
        self.context.send_message.assert_not_awaited()

    async def test_invalid_target_is_rejected_before_send(self):
        for target_type, target_id in (("channel", "123"), ("group", ""), ("private", "abc")):
            with self.assertRaises(ValueError):
                await delivery.send_target(self.context, target_type, target_id, self.chain)
        self.context.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
