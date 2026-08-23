from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal

Category = Literal["update", "try", "read", "noise"]
DecisionKind = Literal["try", "share", "skip_implicit"]


@dataclass(frozen=True)
class RawItem:
    """adapter が返す生アイテム。source 固有の整形はここまでで終える。"""
    source: str
    url: str
    title: str
    excerpt: str = ""
    published_at: datetime | None = None  # tz-aware (UTC 推奨)。日付が取れないソースは None
    metrics: dict[str, int] = field(default_factory=dict)
    lang: str = "en"


def raw_to_dict(r: RawItem) -> dict[str, Any]:
    d = asdict(r)
    d["published_at"] = r.published_at.isoformat() if r.published_at else None
    return d


def raw_from_dict(d: dict[str, Any]) -> RawItem:
    d = dict(d)
    d["published_at"] = datetime.fromisoformat(d["published_at"]) if d.get("published_at") else None
    return RawItem(**d)


@dataclass
class Item:
    """正規化・束ね済みアイテム。id は canonical URL の sha1 先頭 8 桁。"""
    id: str
    url: str
    title: str
    excerpt: str
    published_at: datetime | None
    metrics: dict[str, int]
    lang: str
    group: str
    source: str
    mentions: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat() if self.published_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Item":
        d = dict(d)
        d["published_at"] = datetime.fromisoformat(d["published_at"]) if d.get("published_at") else None
        return cls(**d)


@dataclass
class TriagedItem:
    id: str
    category: Category
    score: int
    signals: dict[str, int]
    reason: str
    try_plan: str = ""
    article_angle: str = ""
    summary: str = ""
    x_draft: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TriagedItem":
        return cls(**d)


@dataclass
class Decision:
    id: str
    decision: DecisionKind
    date: date
    digest_date: date
    title: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "decision": self.decision,
            "date": self.date.isoformat(), "digest_date": self.digest_date.isoformat(),
            "title": self.title, "url": self.url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Decision":
        return cls(
            id=d["id"], decision=d["decision"],
            date=date.fromisoformat(d["date"]), digest_date=date.fromisoformat(d["digest_date"]),
            title=d.get("title", ""), url=d.get("url", ""),
        )
