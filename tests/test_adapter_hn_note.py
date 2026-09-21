import json
from datetime import datetime, timezone
from urllib.parse import parse_qs

import httpx

from ai_watch.adapters.base import TimeWindow
from ai_watch.adapters.hn_algolia import HnAlgoliaAdapter
from ai_watch.adapters.note_search import NoteSearchAdapter
from ai_watch.config import SourceConfig

WINDOW = TimeWindow(start=datetime(2026, 8, 21, tzinfo=timezone.utc), end=datetime(2026, 8, 22, tzinfo=timezone.utc))


def _hn_responder(req: httpx.Request) -> httpx.Response:
    q = parse_qs(req.url.query.decode())
    assert q["tags"] == ["story"]
    assert q["numericFilters"][0].startswith("points>50,created_at_i>")
    hits = {
        "claude": [{"objectID": "1", "title": "Claudette", "url": "https://ex.com/claudette", "points": 221,
                    "num_comments": 80, "created_at_i": 1787000000, "story_text": None}],
        "codex": [{"objectID": "1", "title": "Claudette", "url": "https://ex.com/claudette", "points": 221,
                   "num_comments": 80, "created_at_i": 1787000000, "story_text": None},
                  {"objectID": "2", "title": "Ask HN: Codex?", "url": None, "points": 60, "num_comments": 5,
                   "created_at_i": 1787000100, "story_text": "<p>Is it <i>good</i>?</p>"}],
    }[q["query"][0]]
    return httpx.Response(200, content=json.dumps({"hits": hits}).encode())


def test_hn_dedupes_across_queries_and_falls_back_to_item_url(make_ctx):
    ctx = make_ctx(_hn_responder)
    cfg = SourceConfig(id="hn", type="hn_algolia", group="en", params={"queries": ["claude", "codex"], "min_points": 50})
    items = HnAlgoliaAdapter().fetch(cfg, WINDOW, ctx)
    assert [i.title for i in items] == ["Claudette", "Ask HN: Codex?"]
    assert items[0].metrics == {"points": 221, "comments": 80}
    assert items[0].excerpt.startswith("HN discussion: https://news.ycombinator.com/item?id=1")
    assert items[0].published_at == datetime.fromtimestamp(1787000000, tz=timezone.utc)
    assert items[1].url == "https://news.ycombinator.com/item?id=2"
    assert "Is it good?" in items[1].excerpt


def test_hn_top_min_points_adds_keywordless_high_score_request(make_ctx):
    seen = []

    def responder(req: httpx.Request) -> httpx.Response:
        q = parse_qs(req.url.query.decode(), keep_blank_values=True)
        seen.append((q["query"][0], q["numericFilters"][0].split(",")[0]))
        hits = [{"objectID": "9", "title": "Jev: a System One model", "url": "https://ex.com/jev", "points": 900,
                 "num_comments": 300, "created_at_i": 1787000000, "story_text": None}] if q["query"][0] == "" else []
        return httpx.Response(200, content=json.dumps({"hits": hits}).encode())

    cfg = SourceConfig(id="hn", type="hn_algolia", group="en",
                       params={"queries": ["claude"], "min_points": 50, "top_min_points": 300})
    items = HnAlgoliaAdapter().fetch(cfg, WINDOW, make_ctx(responder))
    assert seen == [("claude", "points>50"), ("", "points>300")]
    assert [i.title for i in items] == ["Jev: a System One model"]


NOTE_JSON = {"data": {"notes": {"total_count": 1, "contents": [
    {"key": "n7f2eb049e349", "name": "Claude Code で事業を作った", "publish_at": "2026-08-21T21:00:00.000+09:00",
     "like_count": 848, "body": "", "description": None, "user": {"urlname": "shiro_life0"}},
    {"key": "nbad", "name": "urlname なし", "publish_at": "2026-08-21T21:00:00.000+09:00", "like_count": 1, "user": {}},
]}}}


def _note_responder(req: httpx.Request) -> httpx.Response:
    q = parse_qs(req.url.query.decode())
    assert req.url.host == "note.com"
    assert req.url.path == "/api/v3/searches"
    assert q["context"] == ["note"]
    assert q["q"] == ["Claude Code"]
    assert q["size"] == ["20"]
    return httpx.Response(200, content=json.dumps(NOTE_JSON).encode())


def test_note_search(make_ctx):
    ctx = make_ctx(_note_responder)
    cfg = SourceConfig(id="note", type="note_search", group="jp", params={"queries": ["Claude Code"], "size": 20})
    items = NoteSearchAdapter().fetch(cfg, WINDOW, ctx)
    assert len(items) == 1
    it = items[0]
    assert it.url == "https://note.com/shiro_life0/n/n7f2eb049e349"
    assert it.metrics == {"likes": 848} and it.lang == "ja"
    assert it.published_at == datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
