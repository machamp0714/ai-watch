import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ai_watch.adapters.base import TimeWindow
from ai_watch.adapters.html_diff import HtmlDiffAdapter
from ai_watch.config import SourceConfig

NOW = datetime.now(timezone.utc)
WINDOW = TimeWindow(start=NOW - timedelta(hours=30), end=NOW)
EPOCH_ISO = "1970-01-01T00:00:00+00:00"
PAGE_V1 = """<html><body>
<a href="/news/first-post">First <b>post</b></a>
<a href="/about">About</a>
<a href="https://www.anthropic.com/news/first-post">dup</a>
</body></html>"""
PAGE_V2 = PAGE_V1.replace("</body>", '<a href="/news/second-post">Second post</a></body>')


def _cfg():
    return SourceConfig(id="anthropic-news", type="html_diff", group="official",
                        params={"url": "https://www.anthropic.com/news", "link_pattern": r"anthropic\.com/news/[a-z0-9-]+"})


def _page_v1_responder(req: httpx.Request) -> httpx.Response:
    assert str(req.url) == "https://www.anthropic.com/news"
    return httpx.Response(200, content=PAGE_V1.encode())


def _page_v2_responder(req: httpx.Request) -> httpx.Response:
    assert str(req.url) == "https://www.anthropic.com/news"
    return httpx.Response(200, content=PAGE_V2.encode())


def test_first_run_records_only(make_ctx, tmp_path):
    ctx = make_ctx(_page_v1_responder)
    assert HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx) == []
    state = json.loads((tmp_path / "data" / "state" / "html_diff" / "anthropic-news.json").read_text())
    assert state == {"https://www.anthropic.com/news/first-post": EPOCH_ISO}


def test_second_run_emits_new_links(make_ctx):
    ctx = make_ctx(_page_v1_responder)
    HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    ctx = make_ctx(_page_v2_responder)
    items = HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    assert len(items) == 1
    assert items[0].url == "https://www.anthropic.com/news/second-post"
    assert items[0].title == "Second post"
    assert items[0].published_at is not None and items[0].published_at.tzinfo is not None


def test_new_link_published_at_is_clamped_to_window_end(make_ctx):
    # window.end は run_nightly の冒頭で固定される一方、first_seen=now() はその後の fetch
    # 実行時刻になるため、必ず window.end より後になる。published_at をそのまま返すと
    # collect() の window.contains() で弾かれ、発見当日に出ない不具合が起きる。
    past_end = NOW - timedelta(hours=1)
    past_window = TimeWindow(start=past_end - timedelta(hours=30), end=past_end)

    ctx = make_ctx(_page_v1_responder)
    HtmlDiffAdapter().fetch(_cfg(), past_window, ctx)
    ctx = make_ctx(_page_v2_responder)
    items = HtmlDiffAdapter().fetch(_cfg(), past_window, ctx)

    assert len(items) == 1
    assert items[0].published_at == past_window.end
    assert past_window.contains(items[0].published_at)


def test_third_run_within_window_emits_again_idempotent(make_ctx):
    ctx = make_ctx(_page_v1_responder)
    HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    ctx = make_ctx(_page_v2_responder)
    HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    ctx = make_ctx(_page_v2_responder)
    items = HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    assert len(items) == 1
    assert items[0].url == "https://www.anthropic.com/news/second-post"


def test_window_excluding_first_seen_emits_nothing(make_ctx):
    ctx = make_ctx(_page_v1_responder)
    HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    ctx = make_ctx(_page_v2_responder)
    HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)   # second-post の first_seen が記録される

    future_window = TimeWindow(start=NOW + timedelta(days=1), end=NOW + timedelta(days=2))
    ctx = make_ctx(_page_v2_responder)
    items = HtmlDiffAdapter().fetch(_cfg(), future_window, ctx)
    assert items == []


def test_legacy_list_state_is_migrated(make_ctx, tmp_path):
    state_path = tmp_path / "data" / "state" / "html_diff" / "anthropic-news.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(["https://www.anthropic.com/news/first-post"]))

    ctx = make_ctx(_page_v2_responder)
    items = HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    assert len(items) == 1 and items[0].url == "https://www.anthropic.com/news/second-post"

    state = json.loads(state_path.read_text())
    assert state["https://www.anthropic.com/news/first-post"] == EPOCH_ISO
    assert "https://www.anthropic.com/news/second-post" in state


def test_zero_matching_links_raises_and_keeps_state(make_ctx, tmp_path):
    ctx = make_ctx(_page_v1_responder)
    HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)

    def _no_match_responder(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "https://www.anthropic.com/news"
        return httpx.Response(200, content="<html><body><a href='/about'>About</a></body></html>".encode())

    ctx = make_ctx(_no_match_responder)
    with pytest.raises(RuntimeError, match="no links matched link_pattern"):
        HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)

    state = json.loads((tmp_path / "data" / "state" / "html_diff" / "anthropic-news.json").read_text())
    assert state == {"https://www.anthropic.com/news/first-post": EPOCH_ISO}
