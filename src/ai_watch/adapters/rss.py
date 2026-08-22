from __future__ import annotations

import time
from typing import Callable

import feedparser

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt, strip_html, struct_to_dt

_DEFAULT_RETRY_WAIT_S = 30
_MAX_RETRY_WAIT_S = 90


class RssAdapter:
    """RSS 2.0 / Atom / RSS 1.0 (RDF)。params: url, lang(既定 en), keywords(任意: title+summary に含む語で絞る)。

    429 を受けたら Retry-After（無ければ x-ratelimit-reset、どちらも無ければ 30 秒）待って
    GET を 1 回だけリトライする（reddit 対策）。それでも 2xx でなければ従来通り例外にする。"""

    def __init__(self, sleep: Callable[[float], None] = time.sleep):
        self._sleep = sleep

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        resp = self._get(cfg.params["url"], ctx)
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

    def _get(self, url: str, ctx: FetchContext):
        resp = ctx.http.get(url)
        if resp.status_code == 429:
            self._sleep(self._retry_wait(resp))
            resp = ctx.http.get(url)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _retry_wait(resp) -> int:
        raw = resp.headers.get("Retry-After") or resp.headers.get("x-ratelimit-reset")
        try:
            wait = int(float(raw)) if raw is not None else _DEFAULT_RETRY_WAIT_S
        except (TypeError, ValueError):
            wait = _DEFAULT_RETRY_WAIT_S
        return min(wait + 1, _MAX_RETRY_WAIT_S)
