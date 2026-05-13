"""
client.py — PandaScore API 封装
负责所有与 PandaScore REST API 的通信
"""

import asyncio
import json
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
            try:
                s = await self._get_session()
                async with s.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=self._timeout,
                ) as r:
                    if r.status == 200:
                        try:
                            return await r.json()
                        except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError) as e:
                            if attempt < 2:
                                logger.warning(
                                    f"[CS] 响应 JSON 解析失败（第{attempt+1}次），5秒后重试: {type(e).__name__}"
                                )
                                await asyncio.sleep(5)
                                continue
                            logger.error(
                                f"[CS] 响应 JSON 解析失败（已重试3次）: {type(e).__name__} {url}"
                            )
                            return None
                    logger.warning(f"[CS] 请求失败: HTTP {r.status} {url}")
                    if r.status >= 500 and attempt < 2:
                        await asyncio.sleep(5)
                        continue
                    return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < 2:
                    logger.warning(
                        f"[CS] 请求异常（第{attempt+1}次），5秒后重试: {type(e).__name__}"
                    )
                    await asyncio.sleep(5)
                else:
                    logger.error(
                        f"[CS] 请求失败（已重试3次）: {type(e).__name__} {url}"
                    )
        return None

    async def fetch_bytes(self, url: str, max_bytes: int = 2 * 1024 * 1024) -> bytes | None:
        if not url.startswith(("http://", "https://")):
            return None
        try:
            s = await self._get_session()
            async with s.get(url, headers={"accept": "*/*"}, timeout=self._timeout) as r:
                if r.status != 200:
                    logger.warning(f"[CS] 图片下载失败: HTTP {r.status} {url}")
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

    async def get_upcoming_matches(self, per_page: int = 50) -> list:
        """获取即将开始的比赛列表"""
        result = await self._request(
            f"{API_BASE}/csgo/matches/upcoming",
            {"per_page": per_page, "sort": "begin_at"},
        )
        return result if isinstance(result, list) else []

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

    async def get_upcoming_tournaments(self, per_page: int = 50) -> list:
        """获取即将开始的赛事（锦标赛）列表"""
        result = await self._request(
            f"{API_BASE}/csgo/tournaments/upcoming",
            {"per_page": per_page, "sort": "begin_at"},
        )
        return result if isinstance(result, list) else []

    async def get_match_result(self, match_id: int) -> dict | None:
        """查询单场比赛结果"""
        result = await self._request(
            f"{API_BASE}/csgo/matches/past",
            {"filter[id]": match_id, "per_page": 1},
        )
        if isinstance(result, list) and result:
            return result[0]
        return None
