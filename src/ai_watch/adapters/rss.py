from __future__ import annotations

import feedparser

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt, strip_html, struct_to_dt


class RssAdapter:
    """RSS 2.0 / Atom / RSS 1.0 (RDF)。params: url, lang(既定 en), keywords(任意: title+summary に含む語で絞る)。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        resp = ctx.http.get(cfg.params["url"])
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        keywords = [k.lower() for k in cfg.params.get("keywords", [])]
        lang = cfg.params.get("lang", "en")

        out: list[RawItem] = []
        for e in feed.entries:
            link = (e.get("link") or "").strip()
            title = (e.get("title") or "").strip()
            if not link or not title:
                continue
            st = e.get("published_parsed") or e.get("updated_parsed")
            summary = e.get("summary") or ""
            if not summary and e.get("content"):
                summary = e["content"][0].get("value", "")
            text = strip_html(summary)
            if keywords and not any(k in f"{title} {text}".lower() for k in keywords):
                continue
            out.append(RawItem(
                source=cfg.id, url=link, title=title, excerpt=excerpt(text),
                published_at=struct_to_dt(st) if st else None, lang=lang,
            ))
        return out
