"""Reusable daily-report PNGs, independent of transport and schedule fetching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


def daily_image_key(day: str, text: str, teams: list, events: list) -> str:
    """Ignore the request clock while retaining all visible schedule content."""
    stable_text = re.sub(
        r"(?m)^(📅 【CS2 赛程日报】\d{2}月\d{2}日) \d{2}:\d{2} 播报$",
        r"\1",
        text,
    )
    payload = {
        "version": 1,
        "day": day,
        "text": stable_text,
        "teams": teams,
        "events": events,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


@dataclass
class CachedImage:
    key: str
    path: Path
    data: bytes


class DailyImageCache:
    """Keep the newest PNG per report window and serialize cache misses.

    Callers receive owned bytes, so deleting replaced files cannot interrupt an
    in-flight QQ upload. Failed renders preserve the previous file. A render
    that lost a race with a schedule update is returned but never published.
    """

    def __init__(self, directory: Path, max_windows: int = 30):
        self.directory = Path(directory)
        self.max_windows = max_windows
        self._entries: OrderedDict[int | str, CachedImage] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def windows(self) -> tuple[int | str, ...]:
        return tuple(self._entries)

    async def get(
        self,
        days: int | str,
        key: str,
        render: Callable[[str], Awaitable[None]],
        is_current: Callable[[], bool],
    ) -> bytes:
        entry = self._entries.get(days)
        if entry and entry.key == key and entry.path.is_file():
            self._entries.move_to_end(days)
            return entry.data
        async with self._lock:
            entry = self._entries.get(days)
            if entry and entry.key == key and entry.path.is_file():
                self._entries.move_to_end(days)
                return entry.data
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / f".building_{uuid4().hex}.png"
            try:
                render_task = asyncio.create_task(render(str(temporary)))
                try:
                    await asyncio.shield(render_task)
                except asyncio.CancelledError:
                    # A Pillow worker may still be writing. Let it finish before
                    # removing the temporary path and stopping the plugin.
                    await render_task
                    raise
                data = temporary.read_bytes()
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("日报图片生成结果不是 PNG")
                if is_current():
                    path = self.directory / f"daily_{days}_{key}.png"
                    temporary.replace(path)
                    self._entries[days] = CachedImage(key, path, data)
                    self._entries.move_to_end(days)
                    while len(self._entries) > self.max_windows:
                        self._entries.popitem(last=False)
                    self._clean_old_files()
                return data
            finally:
                temporary.unlink(missing_ok=True)

    def _clean_old_files(self):
        keep = {entry.path for entry in self._entries.values()}
        for path in self.directory.glob("daily_*.png"):
            if path not in keep:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    async def clear(self):
        async with self._lock:
            self._entries.clear()
            self._clean_old_files()
