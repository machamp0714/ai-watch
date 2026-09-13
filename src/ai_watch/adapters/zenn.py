from __future__ import annotations

from dataclasses import replace
from urllib.parse import quote, urlsplit

import httpx

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt, parse_iso, strip_html
from .rss import RssAdapter


API = "https://zenn.dev/api/articles"


def _api_item(cfg: SourceConfig, value: dict) -> RawItem:
    path = value.get("path")
    title = value.get("title")
    published_at = value.get("published_at")
    parts = [part for part in path.split("/") if part] if isinstance(path, str) else []
    if (
        not isinstance(path, str)
        or len(parts) != 3
        or parts[1] != "articles"
        or path != "/" + "/".join(parts)
    ):
        raise ValueError("Zenn APIの記事URLが不正です")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Zenn APIの記事タイトルが不正です")
    if not isinstance(published_at, str) or not published_at.strip():
        raise ValueError("Zenn APIの公開日時が不正です")
    metrics = {}
    for field, name in (
        ("liked_count", "likes"),
        ("bookmarked_count", "bookmarks"),
        ("comments_count", "comments"),
    ):
        count = value.get(field)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"Zenn APIの{name}が不正です")
        metrics[name] = count
    return RawItem(
        source=cfg.id,
        url=f"https://zenn.dev{path}",
        title=title.strip(),
        published_at=parse_iso(published_at),
        metrics=metrics,
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


def _detail_article(response: httpx.Response) -> dict:
    document = response.json()
    if not isinstance(document, dict) or not isinstance(document.get("article"), dict):
        raise ValueError("Zenn APIの記事詳細形式が不正です")
    return document["article"]


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
        except (httpx.HTTPError, ValueError):
            return list(items.values())
        for rss_item in rss_items:
            api_item = items.get(rss_item.url)
            if api_item is None:
                items[rss_item.url] = rss_item
            else:
                items[rss_item.url] = replace(api_item, excerpt=rss_item.excerpt)
        return list(items.values())

    def fetch_tracked(
        self, cfg: SourceConfig, urls: list[str], ctx: FetchContext
    ) -> list[RawItem]:
        """追跡中のZenn記事を個別APIで再取得する。失敗したURLだけを読み飛ばす。"""
        items = []
        for url in urls:
            parsed = urlsplit(url)
            parts = [part for part in parsed.path.split("/") if part]
            if (
                parsed.scheme != "https"
                or parsed.hostname != "zenn.dev"
                or len(parts) != 3
                or parts[1] != "articles"
            ):
                continue
            try:
                response = ctx.http.get(f"{API}/{quote(parts[2], safe='')}")
                response.raise_for_status()
                value = _detail_article(response)
                item = _api_item(cfg, value)
                body_html = value.get("body_html")
                if not isinstance(body_html, str) or item.url != url:
                    raise ValueError("Zenn APIの記事詳細形式が不正です")
                items.append(replace(item, excerpt=excerpt(strip_html(body_html))))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    continue
                break
            except (httpx.RequestError, ValueError):
                break
        return items
