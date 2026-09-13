from datetime import datetime, timezone

import httpx

from ai_watch.adapters import ADAPTERS
from ai_watch.adapters.base import TimeWindow
from ai_watch.adapters.zenn import ZennAdapter
from ai_watch.config import SourceConfig


WINDOW = TimeWindow(
    start=datetime(2026, 9, 12, tzinfo=timezone.utc),
    end=datetime(2026, 9, 13, tzinfo=timezone.utc),
)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Zenn</title>
<item><title>Zenn APIを試す</title><link>https://zenn.dev/sample/articles/zenn-api</link>
<pubDate>Sat, 12 Sep 2026 10:00:00 GMT</pubDate>
<description><![CDATA[<p>APIとRSSを組み合わせた記録</p>]]></description></item>
</channel></rss>""".encode()


def test_collectからzenn_adapterを選べる():
    assert isinstance(ADAPTERS["zenn"], ZennAdapter)


def _article(path: str, title: str, likes: int, bookmarks: int, comments: int, published_at: str):
    return {
        "id": 1,
        "post_type": "Article",
        "title": title,
        "slug": path.rsplit("/", 1)[-1],
        "comments_count": comments,
        "liked_count": likes,
        "bookmarked_count": bookmarks,
        "body_letters_count": 1200,
        "article_type": "tech",
        "emoji": "🧪",
        "is_suspending": False,
        "published_at": published_at,
        "body_updated_at": published_at,
        "source_repo_updated_at": None,
        "pinned": False,
        "path": path,
        "principal_type": "User",
        "user": {"id": 1, "username": "sample", "name": "架空ユーザー"},
        "publication": None,
    }


def test_apiの人気度にrssの抜粋を補完する(make_ctx):
    calls = []
    latest = _article(
        "/sample/articles/zenn-api",
        "Zenn APIを試す",
        1,
        0,
        0,
        "2026-09-12T19:00:00+09:00",
    )
    popular = _article(
        "/sample/articles/zenn-api",
        "Zenn APIを試す",
        12,
        4,
        2,
        "2026-09-12T19:00:00+09:00",
    )
    older = _article(
        "/sample/articles/growing-later",
        "後から注目された記事",
        35,
        8,
        3,
        "2026-09-08T10:00:00+09:00",
    )

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/articles":
            articles = [latest] if request.url.params["order"] == "latest" else [popular, older]
            return httpx.Response(200, json={"articles": articles, "next_page": None})
        if request.url.path == "/topics/claudecode/feed":
            return httpx.Response(200, content=RSS)
        return httpx.Response(404)

    cfg = SourceConfig(
        id="zenn-claudecode",
        type="zenn",
        group="jp",
        params={
            "topic": "claudecode",
            "url": "https://zenn.dev/topics/claudecode/feed",
            "size": 20,
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch(cfg, WINDOW, make_ctx(responder))

    assert [item.url for item in items] == [
        "https://zenn.dev/sample/articles/zenn-api",
        "https://zenn.dev/sample/articles/growing-later",
    ]
    assert items[0].metrics == {"likes": 12, "bookmarks": 4, "comments": 2}
    assert items[0].excerpt == "APIとRSSを組み合わせた記録"
    assert items[1].metrics == {"likes": 35, "bookmarks": 8, "comments": 3}
    assert items[1].excerpt == ""
    assert items[1].published_at == datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc)
    assert [request.url.params.get("order") for request in calls[:2]] == [
        "latest",
        "liked_count",
    ]


def test_apiが失敗したらrssだけで収集を続ける(make_ctx):
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(403, content=b"blocked")
        if request.url.path == "/topics/claudecode/feed":
            return httpx.Response(200, content=RSS)
        return httpx.Response(404)

    cfg = SourceConfig(
        id="zenn-claudecode",
        type="zenn",
        group="jp",
        params={
            "topic": "claudecode",
            "url": "https://zenn.dev/topics/claudecode/feed",
            "size": 20,
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch(cfg, WINDOW, make_ctx(responder))

    assert len(items) == 1
    assert items[0].url == "https://zenn.dev/sample/articles/zenn-api"
    assert items[0].excerpt == "APIとRSSを組み合わせた記録"
    assert items[0].metrics == {}


def test_rssが失敗してもapiの記事と人気度を返す(make_ctx):
    article = _article(
        "/sample/articles/api-only",
        "APIだけで取得できる記事",
        30,
        6,
        1,
        "2026-09-12T20:00:00+09:00",
    )

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(200, json={"articles": [article], "next_page": None})
        if request.url.path == "/topics/claudecode/feed":
            return httpx.Response(503)
        return httpx.Response(404)

    cfg = SourceConfig(
        id="zenn-claudecode",
        type="zenn",
        group="jp",
        params={
            "topic": "claudecode",
            "url": "https://zenn.dev/topics/claudecode/feed",
            "size": 20,
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch(cfg, WINDOW, make_ctx(responder))

    assert len(items) == 1
    assert items[0].url == "https://zenn.dev/sample/articles/api-only"
    assert items[0].metrics == {"likes": 30, "bookmarks": 6, "comments": 1}
    assert items[0].excerpt == ""


def test_apiのjson形式が壊れていたらrssへフォールバックする(make_ctx):
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(200, content=b"not-json")
        if request.url.path == "/topics/claudecode/feed":
            return httpx.Response(200, content=RSS)
        return httpx.Response(404)

    cfg = SourceConfig(
        id="zenn-claudecode",
        type="zenn",
        group="jp",
        params={
            "topic": "claudecode",
            "url": "https://zenn.dev/topics/claudecode/feed",
            "size": 20,
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch(cfg, WINDOW, make_ctx(responder))

    assert len(items) == 1
    assert items[0].metrics == {}


def test_apiのarticles形式が壊れていたらrssへフォールバックする(make_ctx):
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(200, json={"articles": {"unexpected": "mapping"}})
        if request.url.path == "/topics/claudecode/feed":
            return httpx.Response(200, content=RSS)
        return httpx.Response(404)

    cfg = SourceConfig(
        id="zenn-claudecode",
        type="zenn",
        group="jp",
        params={
            "topic": "claudecode",
            "url": "https://zenn.dev/topics/claudecode/feed",
            "size": 20,
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch(cfg, WINDOW, make_ctx(responder))

    assert len(items) == 1
    assert items[0].metrics == {}


def test_apiに無いrss記事も取りこぼさない(make_ctx):
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(200, json={"articles": [], "next_page": None})
        if request.url.path == "/topics/claudecode/feed":
            return httpx.Response(200, content=RSS)
        return httpx.Response(404)

    cfg = SourceConfig(
        id="zenn-claudecode",
        type="zenn",
        group="jp",
        params={
            "topic": "claudecode",
            "url": "https://zenn.dev/topics/claudecode/feed",
            "size": 20,
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch(cfg, WINDOW, make_ctx(responder))

    assert len(items) == 1
    assert items[0].url == "https://zenn.dev/sample/articles/zenn-api"
    assert items[0].metrics == {}
