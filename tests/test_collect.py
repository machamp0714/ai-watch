import json
import threading
import time
from datetime import date, datetime, timezone

import httpx

from ai_watch.adapters.base import TimeWindow
from ai_watch.collect import collect
from ai_watch.config import Settings, SourceConfig
from ai_watch.watchlist import WatchedTool

RSS_OK = b"""<rss version="2.0"><channel><title>t</title>
<item><title>recent</title><link>https://a/1</link><pubDate>Fri, 21 Aug 2026 10:00:00 GMT</pubDate></item>
<item><title>old</title><link>https://a/2</link><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
<item><title>undated</title><link>https://a/3</link></item>
</channel></rss>"""
RSS_EMPTY = b"<rss version=\"2.0\"><channel><title>empty</title></channel></rss>"
WINDOW = TimeWindow(start=datetime(2026, 8, 20, tzinfo=timezone.utc), end=datetime(2026, 8, 22, tzinfo=timezone.utc))


def _settings(tmp_path, sources):
    return Settings(vault_dir=tmp_path / "vault", data_dir=tmp_path / "data", window_hours=30, user_agent="t",
                    claude_bin="claude", npx_bin="npx", model="sonnet", sources=sources, root=tmp_path)


def _responder(req: httpx.Request) -> httpx.Response:
    if req.url.host == "ok":
        return httpx.Response(200, content=RSS_OK)
    return httpx.Response(403, content=b"nope")


def test_collect_isolates_failures_filters_window_and_saves_raw(make_ctx, tmp_path):
    sources = [
        SourceConfig(id="good", type="rss", group="en", params={"url": "https://ok/feed"}),
        SourceConfig(id="bad", type="rss", group="en", params={"url": "https://fail/feed"}),
        SourceConfig(id="x", type="x_mcp", group="x", params={"urls": []}),
        SourceConfig(id="unknown", type="nope", group="en", params={}),
    ]
    res = collect(_settings(tmp_path, sources), WINDOW, make_ctx(_responder), day=date(2026, 8, 22))
    assert [i.title for i in res.items] == ["recent", "undated"]       # old は window 外、undated は残す
    assert res.counts == {"good": 2}
    assert any(w.startswith("bad: HTTPStatusError") for w in res.warnings)
    assert any(w.startswith("unknown: unknown adapter type") for w in res.warnings)
    assert not any(w.startswith("x:") for w in res.warnings)            # x_mcp は既定で除外
    raw = json.loads((tmp_path / "data" / "raw" / "2026-08-22" / "good.json").read_text())
    assert len(raw) == 3                                                 # raw は window 前の全件


def test_popularity_recheck_source_keeps_items_outside_time_window(make_ctx, tmp_path):
    source = SourceConfig(
        id="popular",
        type="rss",
        group="jp",
        params={"url": "https://ok/feed", "recheck_popularity": True},
    )

    result = collect(
        _settings(tmp_path, [source]),
        WINDOW,
        make_ctx(_responder),
        day=date(2026, 8, 22),
    )

    assert [item.title for item in result.items] == ["recent", "old", "undated"]
    assert result.counts == {"popular": 3}


def test_collect_only_ids(make_ctx, tmp_path):
    sources = [SourceConfig(id="good", type="rss", group="en", params={"url": "https://ok/feed"}),
               SourceConfig(id="bad", type="rss", group="en", params={"url": "https://fail/feed"})]
    res = collect(_settings(tmp_path, sources), WINDOW, make_ctx(_responder), only_ids={"good"}, day=date(2026, 8, 22))
    assert res.warnings == [] and res.counts == {"good": 2}


def test_same_host_sources_run_sequentially(make_ctx, tmp_path):
    calls = []
    lock = threading.Lock()

    def _responder(req: httpx.Request) -> httpx.Response:
        start = time.monotonic()
        time.sleep(0.05)
        end = time.monotonic()
        with lock:
            calls.append((req.url.host, start, end))
        return httpx.Response(200, content=RSS_OK)

    sources = [
        SourceConfig(id="ok1", type="rss", group="en", params={"url": "https://ok/feed1"}),
        SourceConfig(id="ok2", type="rss", group="en", params={"url": "https://ok/feed2"}),
        SourceConfig(id="other", type="rss", group="en", params={"url": "https://other/feed"}),
    ]
    res = collect(_settings(tmp_path, sources), WINDOW, make_ctx(_responder), day=date(2026, 8, 22),
                  same_host_delay_s=0.02)
    assert res.warnings == []

    ok_calls = sorted((s, e) for host, s, e in calls if host == "ok")
    other_calls = [(s, e) for host, s, e in calls if host == "other"]
    assert len(ok_calls) == 2 and len(other_calls) == 1

    (s1, e1), (s2, e2) = ok_calls
    assert s2 >= e1                                              # 同じホストは重複しない

    (os_, oe) = other_calls[0]
    assert any(os_ < e and oe > s for s, e in ok_calls)          # 別ホストは並行して良い


def test_collect_isolates_adapter_internal_keyerror(make_ctx, tmp_path):
    sources = [
        SourceConfig(id="good", type="rss", group="en", params={"url": "https://ok/feed"}),
        SourceConfig(id="nourl", type="rss", group="en", params={}),  # missing url パラメータ
    ]
    res = collect(_settings(tmp_path, sources), WINDOW, make_ctx(_responder), day=date(2026, 8, 22))
    # good source は正常に集められる
    assert [i.title for i in res.items] == ["recent", "undated"]
    assert res.counts == {"good": 2}
    # nourl source の KeyError は warning になり、collect() は継続する
    assert any(w.startswith("nourl: KeyError") for w in res.warnings)


def test_disabled_source_is_not_requested_even_with_only(make_ctx, tmp_path):
    sources = [SourceConfig("good", "rss", "en", {"url": "https://ok/feed"})]
    settings = _settings(tmp_path, sources)
    settings.watchlist = [WatchedTool("tool", "対象", False, (), ("good",))]
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(200, content=RSS_OK)

    result = collect(
        settings, WINDOW, make_ctx(responder), day=date(2026, 8, 22), only_ids={"good"},
    )

    assert calls == []
    assert result.items == []
    assert result.counts == {}
    assert result.warnings == []


def test_enabled_shared_source_is_requested_once(make_ctx, tmp_path):
    sources = [SourceConfig("shared", "rss", "en", {"url": "https://ok/feed"})]
    settings = _settings(tmp_path, sources)
    settings.watchlist = [
        WatchedTool("a", "A", False, (), ("shared",)),
        WatchedTool("b", "B", True, (), ("shared",)),
    ]
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(200, content=RSS_OK)

    result = collect(settings, WINDOW, make_ctx(responder), day=date(2026, 8, 22))

    assert len(calls) == 1
    assert result.counts == {"shared": 2}


def test_all_disabled_owners_do_not_disable_unowned_source(make_ctx, tmp_path):
    sources = [
        SourceConfig("owned", "rss", "en", {"url": "https://owned/feed"}),
        SourceConfig("community", "rss", "en", {"url": "https://community/feed"}),
    ]
    settings = _settings(tmp_path, sources)
    settings.watchlist = [
        WatchedTool("a", "A", False, (), ("owned",)),
        WatchedTool("b", "B", False, (), ("owned",)),
    ]
    hosts = []

    def responder(request):
        hosts.append(request.url.host)
        return httpx.Response(200, content=RSS_OK)

    result = collect(settings, WINDOW, make_ctx(responder), day=date(2026, 8, 22))

    assert hosts == ["community"]
    assert result.counts == {"community": 2}


def test_successful_empty_source_and_failed_source_remain_distinct(make_ctx, tmp_path):
    sources = [
        SourceConfig("empty", "rss", "en", {"url": "https://empty/feed"}),
        SourceConfig("failed", "rss", "en", {"url": "https://failed/feed"}),
    ]

    def responder(request):
        if request.url.host == "empty":
            return httpx.Response(200, content=RSS_EMPTY)
        return httpx.Response(403, content=b"forbidden")

    result = collect(_settings(tmp_path, sources), WINDOW, make_ctx(responder), day=date(2026, 8, 22))

    assert result.counts == {"empty": 0}
    assert "failed" not in result.counts
    assert len(result.warnings) == 1 and result.warnings[0].startswith("failed: HTTPStatusError")
