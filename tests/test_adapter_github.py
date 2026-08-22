import json
from datetime import datetime, timezone

import httpx

from ai_watch.adapters.base import TimeWindow
from ai_watch.adapters.github_releases import GithubReleasesAdapter
from ai_watch.adapters.github_sections import GithubFileSectionsAdapter
from ai_watch.config import SourceConfig

WINDOW = TimeWindow(start=datetime(2026, 8, 20, tzinfo=timezone.utc), end=datetime(2026, 8, 22, tzinfo=timezone.utc))

RELEASES = [
    {"html_url": "https://github.com/openai/codex/releases/tag/rust-v0.50.0", "name": "rust-v0.50.0",
     "tag_name": "rust-v0.50.0", "body": "## What's new\n- cloud tasks", "published_at": "2026-08-21T10:00:00Z", "draft": False},
    {"html_url": "https://github.com/openai/codex/releases/tag/draft", "name": None, "tag_name": "draft",
     "body": "", "published_at": None, "draft": True},
]

CHANGELOG = """# Changelog

## 2.1.239

- Cost estimates now include premium
- Added `/claude-api upgrade`

## 2.1.238

- Fixed a thing

## 2.1.237

- Old
"""


def test_releases(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(200, content=json.dumps(RELEASES).encode()))
    cfg = SourceConfig(id="codex", type="github_releases", group="official",
                       params={"url": "https://api.github.com/repos/openai/codex/releases", "title_prefix": "Codex "})
    items = GithubReleasesAdapter().fetch(cfg, WINDOW, ctx)
    assert len(items) == 1                                  # draft は除外
    it = items[0]
    assert it.title == "Codex rust-v0.50.0"
    assert it.published_at == datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    assert "cloud tasks" in it.excerpt


def test_sections_newest_first_with_fragment_urls(make_ctx):
    ctx = make_ctx(lambda req: httpx.Response(200, content=CHANGELOG.encode()))
    cfg = SourceConfig(id="cc", type="github_file_sections", group="official", params={
        "url": "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md",
        "page_url": "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md",
        "section_pattern": r"^## (\d+\.\d+\.\d+)", "title_prefix": "Claude Code ", "max_sections": 2,
    })
    items = GithubFileSectionsAdapter().fetch(cfg, WINDOW, ctx)
    assert [i.title for i in items] == ["Claude Code 2.1.239", "Claude Code 2.1.238"]
    assert items[0].url == "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md#2.1.239"
    assert items[0].published_at is None                    # 日付なし → seen で新着判定
    assert "Cost estimates" in items[0].excerpt and "Fixed a thing" not in items[0].excerpt
