"""Persistent daily player allocation with a shared, non-repeating pool."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING_TIME = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class DrawResult:
    day: str
    player: dict | None
    repeated: bool
    remaining: int


class DailyPlayerDraw:
    def __init__(self, database: Path, players: list[dict]):
        self.database = Path(database)
        self.players = players
        ids = [p["id"] for p in players]
        names = [p["name"].casefold() for p in players]
        if not players or len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise ValueError("Player IDs and names must be unique in a nonempty pool")

    def draw(self, user_key: str, now: datetime | None = None) -> DrawResult:
        """Allocate once per day, atomically excluding other users' players.

        Args:
            user_key: Stable adapter and sender identity, independent of the group.
            now: Optional timezone-aware time for deterministic offline checks.

        Returns:
            The saved result, a newly allocated player, or an exhausted pool.

        Raises:
            ValueError: The user identity or timezone is missing.
            sqlite3.Error: Storage cannot safely persist the allocation.
        """
        if not user_key:
            raise ValueError("User identity is required")
        current = now or datetime.now(BEIJING_TIME)
        if current.tzinfo is None:
            raise ValueError("Draw time must include a timezone")
        day = current.astimezone(BEIJING_TIME).date().isoformat()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database, timeout=15)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS daily_draws ("
                "day TEXT NOT NULL, user_key TEXT NOT NULL, player_id TEXT NOT NULL, "
                "player_json TEXT NOT NULL, created_at TEXT NOT NULL, "
                "PRIMARY KEY (day, user_key), UNIQUE (day, player_id))"
            )
            # The write lock covers both reading the remaining pool and reserving
            # a result, including requests from separate connections or processes.
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT player_json FROM daily_draws WHERE day=? AND user_key=?",
                    (day, user_key),
                ).fetchone()
                taken = {
                    row[0]
                    for row in db.execute(
                        "SELECT player_id FROM daily_draws WHERE day=?", (day,)
                    )
                }
                available = [p for p in self.players if p["id"] not in taken]
                if existing:
                    result = DrawResult(
                        day, json.loads(existing[0]), True, len(available)
                    )
                elif not available:
                    result = DrawResult(day, None, False, 0)
                else:
                    selected = secrets.choice(available)
                    snapshot = json.dumps(selected, ensure_ascii=False)
                    db.execute(
                        "INSERT INTO daily_draws VALUES (?, ?, ?, ?, ?)",
                        (day, user_key, selected["id"], snapshot, current.isoformat()),
                    )
                    result = DrawResult(
                        day, json.loads(snapshot), False, len(available) - 1
                    )
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise
