import json
from datetime import datetime, timezone

import httpx

from ai_watch.adapters.base import TimeWindow
from ai_watch.adapters.html_diff import HtmlDiffAdapter
from ai_watch.config import SourceConfig

WINDOW = TimeWindow(start=datetime(2026, 8, 20, tzinfo=timezone.utc), end=datetime(2026, 8, 22, tzinfo=timezone.utc))
PAGE_V1 = """<html><body>
<a href="/news/first-post">First <b>post</b></a>
<a href="/about">About</a>
<a href="https://www.anthropic.com/news/first-post">dup</a>
</body></html>"""
PAGE_V2 = PAGE_V1.replace("</body>", '<a href="/news/second-post">Second post</a></body>')


def _cfg():
    return SourceConfig(id="anthropic-news", type="html_diff", group="official",
                        params={"url": "https://www.anthropic.com/news", "link_pattern": r"anthropic\.com/news/[a-z0-9-]+"})


def test_first_run_records_only(make_ctx, tmp_path):
    ctx = make_ctx(lambda req: httpx.Response(200, content=PAGE_V1.encode()))
    assert HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx) == []
    state = json.loads((tmp_path / "data" / "state" / "html_diff" / "anthropic-news.json").read_text())
    assert state == ["https://www.anthropic.com/news/first-post"]


def test_second_run_emits_new_links(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(200, content=PAGE_V1.encode()))
    HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    ctx = make_ctx(lambda req: httpx.Response(200, content=PAGE_V2.encode()))
    items = HtmlDiffAdapter().fetch(_cfg(), WINDOW, ctx)
    assert len(items) == 1
    assert items[0].url == "https://www.anthropic.com/news/second-post"
    assert items[0].title == "Second post"
    assert items[0].published_at is not None and items[0].published_at.tzinfo is not None
