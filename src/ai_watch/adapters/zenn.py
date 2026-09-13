from __future__ import annotations

from dataclasses import replace

import httpx

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, parse_iso
from .rss import RssAdapter


API = "https://zenn.dev/api/articles"


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _api_item(cfg: SourceConfig, value: dict) -> RawItem | None:
    path = value.get("path")
    title = value.get("title")
    if not isinstance(path, str) or not path.startswith("/") or not isinstance(title, str):
        return None
    return RawItem(
        source=cfg.id,
        url=f"https://zenn.dev{path}",
        title=title.strip(),
        published_at=parse_iso(value.get("published_at")),
        metrics={
            "likes": _count(value.get("liked_count")),
            "bookmarks": _count(value.get("bookmarked_count")),
            "comments": _count(value.get("comments_count")),
        },
        lang=cfg.params.get("lang", "ja"),
    )


def _articles(response: httpx.Response) -> list[dict]:
    document = response.json()
    if not isinstance(document, dict) or not isinstance(document.get("articles"), list):
        raise ValueError("Zenn APIの応答形式が不正です")
    articles = document["articles"]
    if not all(isinstance(article, dict) for article in articles):
        raise ValueError("Zenn APIの記事形式が不正です")
    return articles


class ZennAdapter:
    """Zenn APIとRSSから記事を取得する。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        topic = cfg.params["topic"]
        size = int(cfg.params.get("size", 20))
        items: dict[str, RawItem] = {}
        try:
            for order in ("latest", "liked_count"):
                response = ctx.http.get(
                    API,
                    params={"topicname": topic, "order": order, "count": size},
                )
                response.raise_for_status()
                for value in _articles(response):
                    item = _api_item(cfg, value)
                    if item is None:
                        continue
                    current = items.get(item.url)
                    if current is None:
                        items[item.url] = item
                        continue
                    metrics = dict(current.metrics)
                    for key, metric in item.metrics.items():
                        metrics[key] = max(metrics.get(key, 0), metric)
                    items[item.url] = replace(current, metrics=metrics)
        except (httpx.HTTPError, ValueError):
            return RssAdapter().fetch(cfg, window, ctx)

        try:
            rss_items = RssAdapter().fetch(cfg, window, ctx)
        except httpx.HTTPError:
            return list(items.values())
        for rss_item in rss_items:
            api_item = items.get(rss_item.url)
            if api_item is None:
                items[rss_item.url] = rss_item
            else:
                items[rss_item.url] = replace(api_item, excerpt=rss_item.excerpt)
        return list(items.values())
