from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..config import SourceConfig
from ..models import RawItem


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def contains(self, dt: datetime | None) -> bool:
        return dt is not None and self.start <= dt <= self.end

    @classmethod
    def ending_at(cls, end: datetime, hours: int) -> "TimeWindow":
        return cls(start=end - timedelta(hours=hours), end=end)


@dataclass
class FetchContext:
    http: httpx.Client
    data_dir: Path
    root: Path                 # prompts/ schemas/ config/ の基準ディレクトリ
    runner: Any | None = None  # ClaudeRunner（x_mcp のみ使用）


class Adapter(Protocol):
    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]: ...


def make_client(user_agent: str) -> httpx.Client:
    return httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": user_agent})


def struct_to_dt(st: time.struct_time) -> datetime:
    """feedparser の *_parsed（UTC の struct_time）→ aware datetime。"""
    return datetime(*st[:6], tzinfo=timezone.utc)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(s: str) -> str:
    if not s:
        return ""
    p = _TextExtractor()
    p.feed(s)
    return " ".join("".join(p.parts).split())


def excerpt(s: str, n: int = 500) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"
