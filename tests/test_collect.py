import json
import threading
import time
from datetime import date, datetime, timezone

import httpx

from ai_watch.adapters.base import TimeWindow
from ai_watch.collect import collect
from ai_watch.config import Settings, SourceConfig

RSS_OK = b"""<rss version="2.0"><channel><title>t</title>
<item><title>recent</title><link>https://a/1</link><pubDate>Fri, 21 Aug 2026 10:00:00 GMT</pubDate></item>
<item><title>old</title><link>https://a/2</link><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
<item><title>undated</title><link>https://a/3</link></item>
</channel></rss>"""
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
