from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from .models import Item

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  id         TEXT PRIMARY KEY,
  url        TEXT NOT NULL,
  title      TEXT,
  first_seen TEXT NOT NULL,
  shown_on   TEXT,
  category   TEXT,
  max_points INTEGER,
  popularity_source TEXT,
  max_likes INTEGER
);
"""

_POPULARITY_TIERS = (10, 30, 100)


def _popularity_tier(value: int | None) -> int:
    score = max(0, value or 0)
    return sum(score >= threshold for threshold in _POPULARITY_TIERS)


def _is_zenn_article_url(url: str) -> bool:
    parsed = urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https"
        and parsed.hostname == "zenn.dev"
        and len(parts) == 3
        and parts[1] == "articles"
    )


@dataclass(frozen=True)
class ZennRecheckCandidate:
    id: str
    url: str
    source_id: str | None


class SeenStore:
    """機械の既読。通常の再出現を30日止め、未表示記事は人気度の節目で再評価する。"""

    def __init__(self, path: Path, *, migrate_schema: bool = True):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(seen)").fetchall()
        }
        for column, column_type in (
            ("popularity_source", "TEXT"),
            ("max_likes", "INTEGER"),
        ):
            if column not in columns and migrate_schema:
                self.conn.execute(f"ALTER TABLE seen ADD COLUMN {column} {column_type}")
                columns.add(column)
        if migrate_schema:
            self.conn.commit()
        self._has_popularity_source = "popularity_source" in columns
        self._has_max_likes = "max_likes" in columns

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
            popularity_source = bool(
                popularity_sources.intersection(item.mentions)
                or (
                    row is not None
                    and row.get("popularity_source") in popularity_sources
                )
                or (
                    popularity_sources
                    and row is not None
                    and _is_zenn_article_url(row["url"])
                )
            )
            if (
                row is not None
                and popularity_source
                and row["shown_on"] is not None
                and row["shown_on"] != today.isoformat()
            ):
                continue
            if (
                row is not None
                and popularity_source
                and row["shown_on"] is None
                and row["category"] == "noise"
            ):
                if date.fromisoformat(row["first_seen"]) == today:
                    candidates.append(item)
                elif _popularity_tier(item.metrics.get("likes")) > _popularity_tier(
                    row.get("max_likes")
                ):
                    candidates.append(item)
                continue
            if self.is_new(item.id, today, window_days):
                candidates.append(item)
        return candidates

    def zenn_recheck_candidates(
        self, today: date, tracking_days: int = 7
    ) -> list[ZennRecheckCandidate]:
        earliest = (today - timedelta(days=tracking_days)).isoformat()
        popularity_column = (
            "popularity_source" if self._has_popularity_source else "NULL AS popularity_source"
        )
        likes_condition = "COALESCE(max_likes, 0) < ?" if self._has_max_likes else "0 < ?"
        rows = self.conn.execute(
            f"""
            SELECT id, url, {popularity_column}
            FROM seen
            WHERE shown_on IS NULL
              AND category = 'noise'
              AND first_seen >= ?
              AND first_seen < ?
              AND {likes_condition}
            ORDER BY first_seen, id
            """,
            (earliest, today.isoformat(), _POPULARITY_TIERS[-1]),
        ).fetchall()
        candidates = []
        for row in rows:
            if not _is_zenn_article_url(row["url"]):
                continue
            candidates.append(
                ZennRecheckCandidate(
                    id=row["id"],
                    url=row["url"],
                    source_id=row["popularity_source"],
                )
            )
        return candidates

    def mark(
        self,
        items: Iterable[Item],
        today: date,
        shown_ids: set[str],
        categories: dict[str, str],
        *,
        popularity_sources: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        rows = []
        for it in items:
            points = max(it.metrics.values()) if it.metrics else None
            likes = it.metrics.get("likes")
            popularity_source = next(
                (source for source in it.mentions if source in popularity_sources),
                None,
            )
            rows.append(
                (
                    it.id,
                    it.url,
                    it.title,
                    today.isoformat(),
                    today.isoformat() if it.id in shown_ids else None,
                    categories.get(it.id),
                    points,
                    popularity_source,
                    likes,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO seen (
              id, url, title, first_seen, shown_on, category, max_points,
              popularity_source, max_likes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              shown_on   = COALESCE(excluded.shown_on, seen.shown_on),
              category   = COALESCE(excluded.category, seen.category),
              max_points = MAX(COALESCE(excluded.max_points, 0), COALESCE(seen.max_points, 0)),
              popularity_source = COALESCE(excluded.popularity_source, seen.popularity_source),
              max_likes = CASE
                WHEN excluded.max_likes IS NULL THEN seen.max_likes
                ELSE MAX(COALESCE(seen.max_likes, 0), excluded.max_likes)
              END
            """,
            rows,
        )
        self.conn.commit()
