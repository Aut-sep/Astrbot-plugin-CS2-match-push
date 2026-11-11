"""
client.py — PandaScore API 封装
负责所有与 PandaScore REST API 的通信
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import aiohttp

from astrbot.api import logger
from .team_search import TeamSearch
from .tournament_events import build_event
from .push_diagnostics import emit as diagnostic_event

API_BASE = "https://api.pandascore.co"
SCHEDULE_PAGE_SIZE = 25


class PandaScoreClient:
    def __init__(self, token: str):
        self.headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self._timeout = aiohttp.ClientTimeout(total=20)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        self.team_search = TeamSearch(self._search_teams_raw)

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
                    diagnostic_event(self, 'request_failed', level='WARN',
                                     evidence={'endpoint': url.removeprefix(API_BASE), 'http_status': r.status,
                                               'attempt': attempt + 1})
                    if r.status != 429 and r.status < 500:
                        return None
                    if r.status == 429:
                        delay = self._retry_delay(r.headers.get("Retry-After"))
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                diagnostic_event(self, 'request_failed', level='WARN', error=e,
                                 evidence={'endpoint': url.removeprefix(API_BASE), 'attempt': attempt + 1})
                logger.warning(f"[CS] 请求异常（第{attempt+1}次）: {type(e).__name__}")
            except ValueError:
                diagnostic_event(self, 'response_invalid', level='WARN',
                                 evidence={'endpoint': url.removeprefix(API_BASE), 'attempt': attempt + 1})
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
                diagnostic_event(self, 'response_invalid', level='WARN',
                                 evidence={'endpoint': '/csgo/matches/upcoming', 'page': page,
                                           'response_type': type(result).__name__})
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

    async def get_matches_between(
        self, start: datetime, end: datetime, *, per_page: int = 100
    ) -> list | None:
        """All statuses in a UTC schedule window, including past/running games.

        Bounds are inclusive upstream; enforce the half-open interval locally.
        Never return a partial list after a failed or repeated page.
        """
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("expected an aware, nonempty date window")
        rows, seen, previous = [], set(), None
        for page in range(1, 101):
            result = await self._request(
                f"{API_BASE}/csgo/matches",
                {"range[scheduled_at]": f"{start.astimezone(timezone.utc).isoformat()},{end.astimezone(timezone.utc).isoformat()}",
                 "per_page": per_page, "page": page, "sort": "scheduled_at,id"})
            if not isinstance(result, list) or any(not isinstance(m, dict) or m.get("id") is None for m in result):
                return None
            if result and result == previous:
                return None
            for match in result:
                try:
                    scheduled = datetime.fromisoformat(str(match.get("scheduled_at") or "").replace("Z", "+00:00"))
                    if scheduled.tzinfo is None:
                        return None
                except (TypeError, ValueError):
                    return None
                mid = str(match["id"])
                if start <= scheduled < end and mid not in seen:
                    rows.append(match)
                    seen.add(mid)
            if len(result) < per_page:
                return rows
            previous = result
        return None

    async def get_competition_matches(self, event: str) -> list | None:
        """Complete edition/stage inventory; pagination failure is never partial."""
        kind, _, identity = event.partition(":")
        if kind not in {"series", "tournament"} or not identity.isdigit() or int(identity) < 1:
            return None
        resource = "series" if kind == "series" else "tournaments"
        rows, seen, previous = [], set(), None
        for page in range(1, 101):
            result = await self._request(f"{API_BASE}/{resource}/{identity}/matches",
                                         {"per_page": 100, "page": page, "sort": "scheduled_at,id"})
            if not isinstance(result, list) or any(not isinstance(m, dict) or m.get("id") is None for m in result):
                return None
            if result and result == previous:
                return None
            new_ids = {str(m["id"]) for m in result} - seen
            if result and not new_ids:
                return None
            for match in result:
                mid = str(match["id"])
                if mid not in seen:
                    rows.append(match)
                    seen.add(mid)
            if len(result) < 100:
                return rows
            previous = result
        return None

    async def _get_bracket_records(self, path: str, sort: str, params=None) -> list | None:
        rows, seen, previous = [], set(), None
        for page in range(1, 101):
            result = await self._request(f"{API_BASE}/{path}", {**(params or {}), "page": page, "per_page": 100, "sort": sort})
            if not isinstance(result, list) or any(not isinstance(row, dict) or row.get("id") is None for row in result):
                return None
            if result and (result == previous or not ({str(row["id"]) for row in result} - seen)):
                return None
            for row in result:
                if str(row["id"]) not in seen:
                    rows.append(row)
                    seen.add(str(row["id"]))
            if len(result) < 100:
                return rows
            previous = result
        return None

    async def get_series_tournaments(self, serie_id: int) -> list | None:
        if not str(serie_id).isdigit() or int(serie_id) < 1:
            return None
        return await self._get_bracket_records(f"series/{serie_id}/tournaments", "begin_at,id")

    async def get_tournament_brackets(self, tournament_id: int) -> list | None:
        if not str(tournament_id).isdigit() or int(tournament_id) < 1:
            return None
        return await self._get_bracket_records(f"tournaments/{tournament_id}/brackets", "scheduled_at,id")


    async def get_major_series(self, year: int) -> list | None:
        """Discover main Major editions, including Majors hosted in the IEM league."""
        from .cs_major import is_major
        leagues = await self._get_bracket_records('csgo/leagues', 'id', {'search[name]': 'Major'})
        if leagues is None:
            return None
        years = f'{year - 1},{year},{year + 1}'
        rows = await self._get_bracket_records('csgo/series', '-begin_at,id',
                                             {'search[name]': 'Major', 'filter[year]': years})
        if rows is None:
            return None
        for league in leagues:
            if not is_major({'league': league}):
                continue
            editions = await self._get_bracket_records(f"leagues/{league['id']}/series", '-begin_at,id',
                                                      {'filter[year]': years})
            if editions is None:
                return None
            rows.extend({**edition, 'league': league} for edition in editions)
        return list({str(row['id']): row for row in rows if is_major(row)}.values())

    async def search_teams(self, query: str, per_page: int = 20) -> list:
        """Search aliases, Chinese nicknames and typos; prefer main rosters."""
        return await self.team_search.search(query, per_page)

    async def _search_teams_raw(self, query: str, per_page: int = 20) -> list:
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

    async def get_upcoming_events(self, per_page: int = 50) -> list | None:
        """Resolve candidate stages to complete editions, including past stages."""
        candidates = await self.get_upcoming_tournaments(per_page=per_page)
        if candidates is None:
            return None
        seeds = {}
        for tour in candidates:
            sid = (tour.get("serie") or {}).get("id") or tour.get("serie_id")
            if sid:
                seeds.setdefault(str(sid), tour)
        events = []
        for seed in seeds.values():
            event = await self.get_event(seed)
            if event:
                events.append(event)
        return events

    async def get_event(self, seed: dict) -> dict | None:
        """Fetch every stage, including past ones, before deriving an edition."""
        sid = (seed.get("serie") or {}).get("id") or seed.get("serie_id")
        if not sid:
            return None
        stages = []
        page = 1
        while True:
            result = await self._request(
                f"{API_BASE}/series/{sid}/tournaments",
                {"per_page": 100, "page": page, "sort": "begin_at"},
            )
            if not isinstance(result, list):
                # Partial data could turn the next group into a new opening.
                return None
            stages.extend(result)
            if len(result) < 100:
                return build_event(seed, stages)
            page += 1

    async def get_match_result(self, match_id: int) -> dict | None:
        """查询单场比赛当前状态及结果，包括进行中、取消和延期状态。"""
        result = await self._request(f"{API_BASE}/matches/{match_id}", {})
        diagnostic_event(self, 'single_match_received' if isinstance(result, dict) else 'response_invalid',
                         match=result if isinstance(result, dict) else None, match_id=match_id,
                         evidence={'endpoint': f'/matches/{match_id}', 'requested_match_id': match_id},
                         level='INFO' if isinstance(result, dict) else 'WARN')
        return result if isinstance(result, dict) else None
