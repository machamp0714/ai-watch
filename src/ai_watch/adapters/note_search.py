from __future__ import annotations

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt, parse_iso, strip_html

API = "https://note.com/api/v3/searches"


class NoteSearchAdapter:
    """note 非公式検索 API。params: queries(list), size(既定 20)。関連度順なので window 絞りは collect 側に任せる。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        size = int(cfg.params.get("size", 20))
        seen_keys: set[str] = set()
        out: list[RawItem] = []
        for q in cfg.params["queries"]:
            resp = ctx.http.get(API, params={"context": "note", "q": q, "size": size, "start": 0})
            resp.raise_for_status()
            for c in resp.json()["data"]["notes"]["contents"]:
                key = c.get("key")
                urlname = (c.get("user") or {}).get("urlname")
                if not key or not urlname or key in seen_keys:
                    continue
                seen_keys.add(key)
                text = strip_html(c.get("body") or c.get("description") or "")
                out.append(RawItem(
                    source=cfg.id, url=f"https://note.com/{urlname}/n/{key}", title=(c.get("name") or "").strip(),
                    excerpt=excerpt(text), published_at=parse_iso(c.get("publish_at")),
                    metrics={"likes": int(c.get("like_count") or 0)}, lang="ja",
                ))
        return out
