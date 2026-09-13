from datetime import datetime, timezone

import httpx
import pytest

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


def test_rssの形式が壊れていてもapiが成功していれば記事を返す(make_ctx):
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
            return httpx.Response(200, content=b"not-rss")
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
        "https://zenn.dev/sample/articles/api-only"
    ]


def test_apiとrssの両方が壊れていたらrssエラーを通知側へ渡す(make_ctx):
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(503)
        if request.url.path == "/topics/claudecode/feed":
            return httpx.Response(200, content=b"not-rss")
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

    with pytest.raises(ValueError, match="RSSの形式が不正です"):
        ZennAdapter().fetch(cfg, WINDOW, make_ctx(responder))


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


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("path", ""),
        ("path", "/unexpected"),
        ("title", " "),
        ("published_at", 123),
        ("liked_count", "many"),
        ("bookmarked_count", None),
        ("comments_count", True),
    ],
)
def test_apiの記事フィールドが不正ならrssへフォールバックする(
    make_ctx, field, invalid
):
    article = _article(
        "/sample/articles/zenn-api",
        "Zenn APIを試す",
        12,
        4,
        2,
        "2026-09-12T19:00:00+09:00",
    )
    article[field] = invalid

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(200, json={"articles": [article], "next_page": None})
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
    assert items[0].title == "Zenn APIを試す"
    assert items[0].metrics == {}
    assert items[0].excerpt == "APIとRSSを組み合わせた記録"


def test_追跡中の記事を個別apiで再取得する(make_ctx):
    calls = []
    article = _article(
        "/sample/articles/growing-later",
        "後から注目された記事",
        30,
        8,
        3,
        "2026-09-08T10:00:00+09:00",
    )
    article["body_html"] = "<p>公開後に評価が増えた架空記事です。</p>"

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"article": article})

    cfg = SourceConfig(
        id="zenn-llm",
        type="zenn",
        group="jp",
        params={
            "topic": "llm",
            "url": "https://zenn.dev/topics/llm/feed",
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch_tracked(
        cfg,
        ["https://zenn.dev/sample/articles/growing-later"],
        make_ctx(responder),
    )

    assert len(items) == 1
    assert items[0].source == "zenn-llm"
    assert items[0].metrics == {"likes": 30, "bookmarks": 8, "comments": 3}
    assert items[0].excerpt == "公開後に評価が増えた架空記事です。"
    assert calls[0].url.path == "/api/articles/growing-later"


def test_追跡用個別apiの失敗や不正応答は通常収集を止めない(make_ctx):
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("missing"):
            return httpx.Response(404)
        return httpx.Response(200, json={"article": {"unexpected": True}})

    cfg = SourceConfig(
        id="zenn-llm",
        type="zenn",
        group="jp",
        params={
            "topic": "llm",
            "url": "https://zenn.dev/topics/llm/feed",
            "lang": "ja",
        },
    )

    assert ZennAdapter().fetch_tracked(
        cfg,
        [
            "https://zenn.dev/sample/articles/missing",
            "https://zenn.dev/sample/articles/malformed",
            "https://example.com/sample/articles/not-zenn",
        ],
        make_ctx(responder),
    ) == []


def test_追跡用個別apiがtimeoutしたら同じsourceの残りを打ち切る(make_ctx):
    calls = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("timeout", request=request)

    cfg = SourceConfig(
        id="zenn-llm",
        type="zenn",
        group="jp",
        params={
            "topic": "llm",
            "url": "https://zenn.dev/topics/llm/feed",
            "lang": "ja",
        },
    )

    assert ZennAdapter().fetch_tracked(
        cfg,
        [
            "https://zenn.dev/sample/articles/first",
            "https://zenn.dev/sample/articles/second",
            "https://zenn.dev/sample/articles/third",
        ],
        make_ctx(responder),
    ) == []
    assert len(calls) == 1


def test_追跡用個別apiのnot_foundは後続記事の取得を続ける(make_ctx):
    calls = []
    article = _article(
        "/sample/articles/found",
        "取得できた架空記事",
        10,
        2,
        1,
        "2026-09-12T20:00:00+09:00",
    )
    article["body_html"] = "<p>本文</p>"

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("missing"):
            return httpx.Response(404)
        return httpx.Response(200, json={"article": article})

    cfg = SourceConfig(
        id="zenn-llm",
        type="zenn",
        group="jp",
        params={
            "topic": "llm",
            "url": "https://zenn.dev/topics/llm/feed",
            "lang": "ja",
        },
    )

    items = ZennAdapter().fetch_tracked(
        cfg,
        [
            "https://zenn.dev/sample/articles/missing",
            "https://zenn.dev/sample/articles/found",
        ],
        make_ctx(responder),
    )

    assert [item.url for item in items] == ["https://zenn.dev/sample/articles/found"]
    assert len(calls) == 2
