import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

from tests.helpers import load_module


client_module = load_module("client")
PandaScoreClient = client_module.PandaScoreClient


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, chunks=(), length=None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        self.content = FakeContent(chunks)
        self.content_length = length
        self.active = False

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, *_args):
        self.active = False

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return next(self.responses)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = PandaScoreClient("test-token")

    def use_responses(self, *responses):
        session = FakeSession(responses)
        self.client._get_session = AsyncMock(return_value=session)
        return session

    async def test_rate_limit_releases_response_before_retry(self):
        limited = FakeResponse(429, headers={"Retry-After": "2"})
        self.use_responses(limited, FakeResponse(payload=[{"id": 1}]))

        async def sleep(delay):
            self.assertEqual(delay, 2)
            self.assertFalse(limited.active)

        with patch.object(client_module.asyncio, "sleep", side_effect=sleep):
            self.assertEqual(await self.client._request("https://example.test", {}), [{"id": 1}])

    async def test_invalid_json_and_server_errors_retry_then_succeed(self):
        session = self.use_responses(
            FakeResponse(payload=ValueError("bad JSON")),
            FakeResponse(503),
            FakeResponse(payload={"id": 1}),
        )
        with patch.object(client_module.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            self.assertEqual(await self.client._request("https://example.test", {}), {"id": 1})
            self.assertEqual(sleep.await_count, 2)
        self.assertEqual(len(session.requests), 3)

    async def test_auth_failure_is_not_retried(self):
        session = self.use_responses(FakeResponse(401))
        self.assertIsNone(await self.client._request("https://example.test", {}))
        self.assertEqual(len(session.requests), 1)

    async def test_cancellation_is_propagated(self):
        self.client._get_session = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.client._request("https://example.test", {})

    async def test_fetch_failure_differs_from_empty_schedule(self):
        self.client._request = AsyncMock(side_effect=[None, [], None, []])
        self.assertIsNone(await self.client.get_upcoming_matches())
        self.assertEqual(await self.client.get_upcoming_matches(), [])
        self.assertIsNone(await self.client.get_upcoming_tournaments())
        self.assertEqual(await self.client.get_upcoming_tournaments(), [])

    async def test_pagination_includes_exact_cutoff_and_stops_after_it(self):
        cutoff = datetime(2026, 10, 8, 12, tzinfo=timezone(timedelta(hours=8)))
        self.client._request = AsyncMock(side_effect=[
            [{"id": 1, "scheduled_at": None}, {"id": 2, "scheduled_at": "2026-10-08T03:00:00Z"}],
            [{"id": 3, "scheduled_at": "2026-10-08T04:00:00Z"},
             {"id": 4, "scheduled_at": "2026-10-08T04:00:01Z"}],
        ])
        result = await self.client.get_upcoming_matches(per_page=2, until=cutoff)
        self.assertEqual([match["id"] for match in result], [1, 2, 3])
        params = [call.args[1] for call in self.client._request.await_args_list]
        self.assertEqual([p["page"] for p in params], [1, 2])
        self.assertTrue(all(p["sort"] == "scheduled_at,id" for p in params))

    async def test_partial_page_failure_does_not_publish_partial_schedule(self):
        self.client._request = AsyncMock(side_effect=[[{"id": 1}], None])
        self.assertIsNone(await self.client.get_upcoming_matches(per_page=1, until=datetime.now(timezone.utc)))

    async def test_repeated_full_page_does_not_loop(self):
        self.client._request = AsyncMock(return_value=[{"id": 1}])
        self.assertIsNone(await self.client.get_upcoming_matches(per_page=1, until=datetime.now(timezone.utc)))
        self.assertEqual(self.client._request.await_count, 2)

    async def test_single_match_reads_current_status(self):
        self.client._request = AsyncMock(return_value={"id": 10, "status": "canceled"})
        self.assertEqual((await self.client.get_match_result(10))["status"], "canceled")
        self.client._request.assert_awaited_once_with(f"{client_module.API_BASE}/matches/10", {})

    async def test_image_limit_checks_both_headers_and_stream(self):
        self.use_responses(FakeResponse(length=10), FakeResponse(chunks=(b"123", b"456")))
        self.assertIsNone(await self.client.fetch_bytes("https://example.test/img", max_bytes=5))
        self.assertIsNone(await self.client.fetch_bytes("https://example.test/img", max_bytes=5))

    async def test_image_auth_header_does_not_expose_api_token(self):
        session = self.use_responses(FakeResponse(chunks=(b"image",)))
        self.assertEqual(await self.client.fetch_bytes("https://example.test/img"), b"image")
        self.assertNotIn("Authorization", session.requests[0][1]["headers"])

    def test_retry_after_bad_or_excessive_values_are_bounded(self):
        self.assertEqual(self.client._retry_delay("invalid"), 5)
        self.assertEqual(self.client._retry_delay("5000"), 60)
        self.assertEqual(self.client._retry_delay("-1"), 0)

    def test_retry_after_http_date(self):
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        delay = self.client._retry_delay(format_datetime(retry_at, usegmt=True))
        self.assertGreater(delay, 28)
        self.assertLessEqual(delay, 30)


if __name__ == "__main__":
    unittest.main()
