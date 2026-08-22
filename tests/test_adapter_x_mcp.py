import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_watch.adapters.base import TimeWindow
from ai_watch.adapters.x_mcp import READ_ONLY_TOOLS, XMcpAdapter
from ai_watch.claude_runner import ClaudeResult
from ai_watch.config import SourceConfig

WINDOW = TimeWindow(start=datetime(2026, 8, 21, tzinfo=timezone.utc), end=datetime(2026, 8, 22, tzinfo=timezone.utc))


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, prompt, schema, **kw):
        self.calls.append((prompt, schema, kw))
        return self.result


def _prepare(tmp_path: Path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "x_collect.md").write_text("URLS:\n{{URLS}}\nSINCE {{WINDOW_START}} MAX {{MAX_POSTS}}")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "x_items.schema.json").write_text(json.dumps({"type": "object"}))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "playwright-mcp.json").write_text("{}")


def _cfg():
    return SourceConfig(id="x", type="x_mcp", group="x",
                        params={"urls": ["https://x.com/i/bookmarks"], "max_posts": 10, "budget_usd": 1.5})


def test_maps_posts_and_uses_single_link_as_url(make_ctx, tmp_path):
    _prepare(tmp_path)
    data = {"logged_in": True, "posts": [
        {"url": "https://x.com/a/status/1", "author": "a", "text": "New Claude Code hooks! https://example.com/p",
         "posted_at": "2026-08-21T10:00:00+00:00", "likes": 10, "reposts": 2, "links": ["https://example.com/p"], "lang": "en"},
        {"url": "https://x.com/b/status/2", "author": "b", "text": "二つリンク", "posted_at": "",
         "links": ["https://e.com/1", "https://e.com/2"], "lang": "ja"},
    ]}
    runner = FakeRunner(ClaudeResult(True, data, 0.3, "success"))
    items = XMcpAdapter().fetch(_cfg(), WINDOW, make_ctx(lambda r: None, runner=runner))
    assert items[0].url == "https://example.com/p"
    assert "X post: https://x.com/a/status/1" in items[0].excerpt
    assert items[0].title.startswith("@a: New Claude Code hooks!")
    assert items[0].metrics == {"likes": 10, "reposts": 2}
    assert items[0].published_at == datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    assert items[1].url == "https://x.com/b/status/2" and items[1].published_at is None and items[1].lang == "ja"

    prompt, schema, kw = runner.calls[0]
    assert "- https://x.com/i/bookmarks" in prompt and "MAX 10" in prompt and "2026-08-21" in prompt
    assert kw["budget_usd"] == 1.5 and kw["allowed_tools"] == READ_ONLY_TOOLS
    assert kw["mcp_config"] == tmp_path / "config" / "playwright-mcp.json"


def test_not_logged_in_raises(make_ctx, tmp_path):
    _prepare(tmp_path)
    runner = FakeRunner(ClaudeResult(True, {"logged_in": False, "posts": [], "notes": "login form"}, 0.1, "success"))
    with pytest.raises(RuntimeError, match="not logged in"):
        XMcpAdapter().fetch(_cfg(), WINDOW, make_ctx(lambda r: None, runner=runner))


def test_runner_failure_raises(make_ctx, tmp_path):
    _prepare(tmp_path)
    runner = FakeRunner(ClaudeResult(False, None, 1.5, "error_max_budget_usd", error="budget"))
    with pytest.raises(RuntimeError, match="x-collect failed"):
        XMcpAdapter().fetch(_cfg(), WINDOW, make_ctx(lambda r: None, runner=runner))


def test_read_only_tools_exclude_mutating_tools():
    joined = " ".join(READ_ONLY_TOOLS)
    for forbidden in ("click", "type", "fill_form", "file_upload", "drag", "select_option"):
        assert forbidden not in joined


def test_long_post_keeps_x_post_line(make_ctx, tmp_path):
    _prepare(tmp_path)
    data = {"logged_in": True, "posts": [
        {"url": "https://x.com/a/status/9", "author": "a", "text": "x" * 1500, "posted_at": "",
         "links": ["https://example.com/p"], "lang": "en"},
    ]}
    runner = FakeRunner(ClaudeResult(True, data, 0.3, "success"))
    items = XMcpAdapter().fetch(_cfg(), WINDOW, make_ctx(lambda r: None, runner=runner))
    assert "X post: https://x.com/a/status/9" in items[0].excerpt
    assert len(items[0].excerpt) <= 600
