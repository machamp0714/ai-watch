from __future__ import annotations

from datetime import datetime, timezone

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt, strip_html

API = "https://hn.algolia.com/api/v1/search_by_date"


class HnAlgoliaAdapter:
    """HN Algolia API。params: queries(list), min_points(既定 50), top_min_points(任意)。
    window.start 以降・points>min を API 側で絞る。top_min_points があれば、キーワードなしで
    それを超える高得点ストーリーも取る（クエリに無い新しい名前の話題を拾うため）。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        min_points = int(cfg.params.get("min_points", 50))
        requests = [(q, min_points) for q in cfg.params["queries"]]
        if cfg.params.get("top_min_points") is not None:
            requests.append(("", int(cfg.params["top_min_points"])))
        since = int(window.start.timestamp())
        seen_ids: set[str] = set()
        out: list[RawItem] = []
        for q, points in requests:
            resp = ctx.http.get(API, params={
                "query": q, "tags": "story", "hitsPerPage": 50,
                "numericFilters": f"points>{points},created_at_i>{since}",
            })
            resp.raise_for_status()
            for h in resp.json().get("hits", []):
                oid = str(h["objectID"])
                if oid in seen_ids:
                    continue
                seen_ids.add(oid)
                hn_url = f"https://news.ycombinator.com/item?id={oid}"
                text = strip_html(h.get("story_text") or "")
                out.append(RawItem(
                    source=cfg.id, url=h.get("url") or hn_url, title=(h.get("title") or "").strip(),
                    excerpt=f"HN discussion: {hn_url}" + (f"\n{excerpt(text)}" if text else ""),
                    published_at=datetime.fromtimestamp(int(h["created_at_i"]), tz=timezone.utc),
                    metrics={"points": int(h.get("points") or 0), "comments": int(h.get("num_comments") or 0)},
                    lang="en",
                ))
        return out
