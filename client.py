"""
client.py — PandaScore API 封装
负责所有与 PandaScore REST API 的通信
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import aiohttp

from astrbot.api import logger

API_BASE = "https://api.pandascore.co"


class PandaScoreClient:
    def __init__(self, token: str):
        self.headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self._timeout = aiohttp.ClientTimeout(total=20)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建复用的 ClientSession"""
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """关闭 ClientSession"""
        async with self._session_lock:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None

    async def _request(self, url: str, params: dict) -> dict | list | None:
        for attempt in range(3):
            delay = 5.0
            try:
                s = await self._get_session()
                async with s.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=self._timeout,
                ) as r:
                    if r.status == 200:
                        return await r.json()
                    logger.warning(f"[CS] 请求失败: HTTP {r.status} {url}")
                    if r.status != 429 and r.status < 500:
                        return None
                    if r.status == 429:
                        delay = self._retry_delay(r.headers.get("Retry-After"))
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"[CS] 请求异常（第{attempt+1}次）: {type(e).__name__}")
            except ValueError:
                logger.warning(f"[CS] API 返回了无效 JSON: {url}")
            # 先退出响应上下文，释放连接后再等待，避免占用连接池。
            if attempt < 2:
                await asyncio.sleep(delay)
        logger.error(f"[CS] 请求失败（已尝试3次）: {url}")
        return None

    @staticmethod
    def _retry_delay(retry_after: str | None) -> float:
        """支持 Retry-After 的秒数/HTTP 日期格式，并限制单次等待时间。"""
        if not retry_after:
            return 5.0
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return 5.0
        return max(0.0, min(delay, 60.0))

    async def fetch_bytes(self, url: str, max_bytes: int = 2 * 1024 * 1024) -> bytes | None:
        if not url.startswith(("http://", "https://")):
            return None
        try:
            s = await self._get_session()
            async with s.get(url, headers={"accept": "*/*"}, timeout=self._timeout) as r:
                if r.status != 200:
                    logger.warning(f"[CS] 图片下载失败: HTTP {r.status} {url}")
                    return None
                if r.content_length is not None and r.content_length > max_bytes:
                    logger.warning(f"[CS] 图片过大，已跳过: {url}")
                    return None
                chunks = []
                total = 0
                async for chunk in r.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        logger.warning(f"[CS] 图片过大，已跳过: {url}")
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"[CS] 图片下载异常: {type(e).__name__} {url}")
            return None

    async def get_upcoming_matches(
        self, per_page: int = 50, *, until: datetime | None = None
    ) -> list | None:
        """读取赛程；指定截止时间时自动分页，失败返回 None（区别于空赛程）。"""
        page_size = max(1, min(int(per_page), 100))
        if until is not None:
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            until = until.astimezone(timezone.utc)
        matches = []
        seen_ids = set()
        previous_page = None
        page = 1
        while True:
            result = await self._request(
                f"{API_BASE}/csgo/matches/upcoming",
                {"per_page": page_size, "page": page, "sort": "scheduled_at,id"},
            )
            if not isinstance(result, list) or any(not isinstance(m, dict) for m in result):
                return None
            if result and result == previous_page:
                logger.warning("[CS] API 返回重复分页，保留原有赛程")
                return None
            for match in result:
                scheduled_at = match.get("scheduled_at")
                if until is not None and scheduled_at:
                    try:
                        dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt > until:
                            return matches
                    except (TypeError, ValueError, AttributeError):
                        pass
                mid = match.get("id")
                if mid is None or mid not in seen_ids:
                    matches.append(match)
                    if mid is not None:
                        seen_ids.add(mid)
            if until is None or len(result) < page_size:
                return matches
            previous_page = result
            page += 1

    async def search_teams(self, query: str, per_page: int = 20) -> list:
        """按名称搜索 CS2 战队，返回精确匹配列表"""
        result = await self._request(
            f"{API_BASE}/csgo/teams",
            {"search[name]": query, "per_page": per_page, "sort": "name"},
        )
        return result if isinstance(result, list) else []

    async def get_team(self, team_id_or_slug: int | str) -> dict | None:
        result = await self._request(f"{API_BASE}/teams/{team_id_or_slug}", {})
        return result if isinstance(result, dict) else None

    async def get_upcoming_tournaments(self, per_page: int = 50) -> list | None:
        """获取即将开始的赛事（锦标赛）列表"""
        result = await self._request(
            f"{API_BASE}/csgo/tournaments/upcoming",
            {"per_page": per_page, "sort": "begin_at"},
        )
        return result if isinstance(result, list) else None

    async def get_match_result(self, match_id: int) -> dict | None:
        """查询单场比赛当前状态及结果，包括进行中、取消和延期状态。"""
        result = await self._request(f"{API_BASE}/matches/{match_id}", {})
        return result if isinstance(result, dict) else None
