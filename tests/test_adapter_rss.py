from datetime import datetime, timezone

import httpx
import pytest

from ai_watch.adapters.base import TimeWindow
from ai_watch.adapters.rss import RssAdapter
from ai_watch.config import SourceConfig

RSS2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Zenn</title>
<item><title>Claude Code の hooks 入門</title><link>https://zenn.dev/a/articles/hooks</link>
<pubDate>Fri, 21 Aug 2026 10:00:00 GMT</pubDate>
<description><![CDATA[<p>hooks を<b>試した</b>記録</p>]]></description></item>
<item><title>無関係な記事</title><link>https://zenn.dev/a/articles/other</link>
<pubDate>Fri, 21 Aug 2026 09:00:00 GMT</pubDate><description>Rails の話</description></item>
</channel></rss>"""

RDF = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="https://b.hatena.ne.jp/q/claude"><title>hatena</title></channel>
<item rdf:about="https://example.com/x"><title>はてブ記事</title><link>https://example.com/x</link>
<dc:date>2026-08-21T12:34:56+09:00</dc:date><description>desc</description></item>
</rdf:RDF>"""

WINDOW = TimeWindow(start=datetime(2026, 8, 20, tzinfo=timezone.utc), end=datetime(2026, 8, 22, tzinfo=timezone.utc))


def test_rss2_basic(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(200, content=RSS2.encode()))
    cfg = SourceConfig(id="zenn", type="rss", group="jp", params={"url": "https://zenn.dev/feed", "lang": "ja"})
    items = RssAdapter().fetch(cfg, WINDOW, ctx)
    assert [i.title for i in items] == ["Claude Code の hooks 入門", "無関係な記事"]
    it = items[0]
    assert it.source == "zenn" and it.lang == "ja"
    assert it.url == "https://zenn.dev/a/articles/hooks"
    assert it.published_at == datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    assert it.excerpt == "hooks を試した記録"          # HTML は剥がす


def test_rss_keywords_filter(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(200, content=RSS2.encode()))
    cfg = SourceConfig(id="hot", type="rss", group="jp",
                       params={"url": "https://x/feed", "keywords": ["claude", "codex"]})
    items = RssAdapter().fetch(cfg, WINDOW, ctx)
    assert [i.title for i in items] == ["Claude Code の hooks 入門"]


def test_rdf_dc_date(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(200, content=RDF.encode()))
    cfg = SourceConfig(id="hatena", type="rss", group="jp", params={"url": "https://x/rss"})
    items = RssAdapter().fetch(cfg, WINDOW, ctx)
    assert items[0].published_at == datetime(2026, 8, 21, 3, 34, 56, tzinfo=timezone.utc)


def test_http_error_raises(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(403, content=b"blocked"))
    cfg = SourceConfig(id="r", type="rss", group="en", params={"url": "https://x/rss"})
    try:
        RssAdapter().fetch(cfg, WINDOW, ctx)
        assert False, "should raise"
    except httpx.HTTPStatusError:
        pass


def test_broken_rss_raises_instead_of_looking_like_an_empty_feed(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(200, content=b"not-rss"))
    cfg = SourceConfig(id="r", type="rss", group="en", params={"url": "https://x/rss"})

    with pytest.raises(ValueError, match="RSSの形式が不正です"):
        RssAdapter().fetch(cfg, WINDOW, ctx)


def test_rss_retries_once_on_429_with_reset_header(make_ctx):
    calls = []
    sleeps = []

    def _responder(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, headers={"x-ratelimit-reset": "24"})
        return httpx.Response(200, content=RSS2.encode())

    ctx = make_ctx(_responder)
    cfg = SourceConfig(id="reddit-claudeai", type="rss", group="en", params={"url": "https://www.reddit.com/feed"})
    adapter = RssAdapter(sleep=lambda s: sleeps.append(s))
    items = adapter.fetch(cfg, WINDOW, ctx)
    assert [i.title for i in items] == ["Claude Code の hooks 入門", "無関係な記事"]
    assert sleeps == [25]
    assert len(calls) == 2


def test_rss_429_twice_raises(make_ctx):
    sleeps = []

    def _responder(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "5"})

    ctx = make_ctx(_responder)
    cfg = SourceConfig(id="reddit-claudeai", type="rss", group="en", params={"url": "https://www.reddit.com/feed"})
    adapter = RssAdapter(sleep=lambda s: sleeps.append(s))
    try:
        adapter.fetch(cfg, WINDOW, ctx)
        assert False, "should raise"
    except httpx.HTTPStatusError:
        pass
    assert sleeps == [6]
