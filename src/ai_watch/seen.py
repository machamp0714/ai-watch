from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .models import Item

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  id         TEXT PRIMARY KEY,
  url        TEXT NOT NULL,
  title      TEXT,
  first_seen TEXT NOT NULL,
  shown_on   TEXT,
  category   TEXT,
  max_points INTEGER
);
"""

_POPULARITY_TIERS = (10, 30, 100)


def _popularity_tier(value: int | None) -> int:
    score = max(0, value or 0)
    return sum(score >= threshold for threshold in _POPULARITY_TIERS)


class SeenStore:
    """機械の既読。通常の再出現を30日止め、未表示記事は人気度の節目で再評価する。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def __enter__(self) -> "SeenStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def get(self, item_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM seen WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def is_new(self, item_id: str, today: date, window_days: int = 30) -> bool:
        row = self.get(item_id)
        if row is None:
            return True
        first_seen = date.fromisoformat(row["first_seen"])
        if first_seen == today:
            return True  # 同日の再実行（--from triage 等）で全件消えないように
        return first_seen < today - timedelta(days=window_days)

    def filter_new(
        self,
        items: Iterable[Item],
        today: date,
        window_days: int = 30,
        *,
        popularity_sources: set[str] | frozenset[str] = frozenset(),
    ) -> list[Item]:
        candidates = []
        for item in items:
            row = self.get(item.id)
            popularity_source = bool(popularity_sources.intersection(item.mentions))
            if (
                row is not None
                and popularity_source
                and row["shown_on"] is not None
                and row["shown_on"] != today.isoformat()
            ):
                continue
            if self.is_new(item.id, today, window_days):
                candidates.append(item)
                continue
            if (
                row is not None
                and row["shown_on"] is None
                and row["category"] == "noise"
                and popularity_source
                and _popularity_tier(item.metrics.get("likes"))
                > _popularity_tier(row["max_points"])
            ):
                candidates.append(item)
        return candidates

    def mark(self, items: Iterable[Item], today: date, shown_ids: set[str], categories: dict[str, str]) -> None:
        rows = []
        for it in items:
            points = (
                it.metrics["likes"]
                if "likes" in it.metrics
                else max(it.metrics.values()) if it.metrics else None
            )
            rows.append((
                it.id, it.url, it.title, today.isoformat(),
                today.isoformat() if it.id in shown_ids else None,
                categories.get(it.id), points,
            ))
        self.conn.executemany(
            """
            INSERT INTO seen (id, url, title, first_seen, shown_on, category, max_points)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              shown_on   = COALESCE(excluded.shown_on, seen.shown_on),
              category   = COALESCE(excluded.category, seen.category),
              max_points = MAX(COALESCE(excluded.max_points, 0), COALESCE(seen.max_points, 0))
            """,
            rows,
        )
        self.conn.commit()
