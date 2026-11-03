"""Prebuild current-stage charts and atomically publish complete responses."""
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time

from astrbot.api import logger

from .bracket_renderer import BracketRenderer
from .bracket_stage import current_stages
from .daily_image_cache import DailyImageCache
from .formatters import now_utc


SLOTS = {'current': 0, 'all': 1, 'swiss': 3, 'knockout': 4,
         'stage1': 5, 'stage2': 6, 'stage3': 7}


def selected_stages(snapshot, selected):
    stages = snapshot['stages']
    if selected == 'current':
        return current_stages(stages, now_utc())
    return [stage for stage in stages if selected == 'all'
            or stage.get('key', stage['kind']) == selected
            or (selected == 'swiss' and stage['kind'] == 'swiss')]


@dataclass
class PreparedBracket:
    snapshot: dict
    stages: list
    data: bytes | None
    token: str = field(repr=False)
    images: bool
    checked_at: float


class BracketImages:
    def __init__(self, plugin, service):
        self.plugin, self.service = plugin, service
        self.images = DailyImageCache(Path(plugin._banner_dir) / 'bracket_images', max_windows=8)
        self.ready = None
        self._account = None
        self._refresh_lock = asyncio.Lock()
        self._wake = asyncio.Event()

    def request_refresh(self):
        self._wake.set()

    def _usable(self, check_stage=True):
        ready = self.ready
        if not ready or ready.token != self.plugin._token or ready.images != bool(self.plugin.store.get('image_push_enabled', True)):
            return False
        if time.monotonic() - ready.checked_at > 600:
            return False
        return not check_stage or [s['id'] for s in ready.stages] == [
            s['id'] for s in selected_stages(ready.snapshot, 'current')]

    def _image_key(self, snapshot, stages):
        # The fetch clock is not a content change. Image URLs and local overrides are.
        teams = self.plugin._notice_teams(matches=[m for stage in stages for m in stage['matches']])
        local = []
        for team in teams:
            data = self.plugin.team_logos.get(team['name']) or self.plugin.team_logos.get(team.get('acronym', ''))
            local.append((team['name'], hashlib.sha256(data).hexdigest() if data else None))
        payload = {'version': 2, 'game': 'csgo', 'title': snapshot['title'],
                   'stages': stages, 'local_marks': local}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    async def _image(self, snapshot, stages, selected, token, enabled=None):
        key = self._image_key(snapshot, stages)

        async def render(path):
            matches = [m for stage in stages for m in stage['matches']]
            marks = await self.plugin._resolve_team_marks(self.plugin._notice_teams(matches=matches))
            await asyncio.to_thread(BracketRenderer(self.plugin._banner_renderer).render,
                                    snapshot, stages, path, marks)

        return await self.images.get(SLOTS[selected], key, render,
                                     lambda: token == self.plugin._token
                                     and (self.plugin.store.get('image_push_enabled', True) if enabled is None else enabled))

    async def refresh(self, only_if_missing=False):
        async with self._refresh_lock:
            if only_if_missing and self._usable():
                return True
            token = self.plugin._token
            if self._account != token:
                self.ready = None
                self.service.cached = None
                self._account = token
                await self.images.clear()
            if not token:
                return False
            enabled = bool(self.plugin.store.get('image_push_enabled', True))
            snapshot = await self.service.fetch()
            if snapshot is None:
                return False
            stages = selected_stages(snapshot, 'current')
            data = await self._image(snapshot, stages, 'current', token) if stages and enabled else None
            if token != self.plugin._token or enabled != bool(self.plugin.store.get('image_push_enabled', True)):
                return False
            if not enabled:
                await self.images.clear()
            # Publish only after the new PNG is complete. Requests can use the old
            # owned bytes throughout refresh, including while old files are removed.
            self.ready = PreparedBracket(snapshot, stages, data, token, enabled, time.monotonic())
            return True

    async def get(self, selected, *, images_enabled=None):
        try:
            return await self._get(selected, images_enabled=images_enabled)
        except (OSError, RuntimeError) as exc:
            raise ValueError('bracket image unavailable') from exc

    async def _get(self, selected, *, images_enabled=None):
        if not self._usable(check_stage=selected == 'current'):
            if not await self.refresh(only_if_missing=True):
                raise ValueError('bracket preparation unavailable')
        ready = self.ready
        if selected == 'current' and images_enabled is None:
            return ready.snapshot, ready.stages, ready.data
        stages = selected_stages(ready.snapshot, selected)
        enabled = ready.images if images_enabled is None else images_enabled
        data = await self._image(ready.snapshot, stages, selected, ready.token, enabled=images_enabled) if stages and enabled else None
        return ready.snapshot, stages, data

    async def run(self):
        while True:
            self._wake.clear()
            try:
                if self.plugin._token and self.plugin.store.get('image_push_enabled', True):
                    previous = self.ready
                    if await asyncio.wait_for(self.refresh(), timeout=90):
                        if self.ready and (previous is None or previous.data is not self.ready.data):
                            status = '当前阶段图片已预生成' if self.ready.data else '当前无进行阶段，提示已缓存'
                            logger.info(f'[CS] 对阵图缓存就绪：{status}')
                else:
                    await self.images.clear()
                    if self.ready and (self.ready.token != self.plugin._token or self.ready.images):
                        self.ready = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f'[CS] 对阵图预生成暂未完成: {type(exc).__name__}')
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=120)
            except asyncio.TimeoutError:
                pass
