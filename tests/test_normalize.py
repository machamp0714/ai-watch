from datetime import datetime, timezone

from ai_watch.models import RawItem
from ai_watch.normalize import canonical_url, item_id, normalize


def test_canonical_url_strips_tracking_and_www():
    assert canonical_url("http://www.Example.com/a/?utm_source=x&b=1&fbclid=z") == "https://example.com/a?b=1"


def test_canonical_url_keeps_fragment_for_changelog_sections():
    u = "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md#2.1.239"
    assert canonical_url(u) == u


def test_canonical_url_twitter_to_x_and_strips_s_t():
    assert canonical_url("https://twitter.com/a/status/1?s=20&t=abc") == "https://x.com/a/status/1"


def test_item_id_is_stable_and_prefixed():
    a = item_id("https://example.com/a?utm_source=1")
    b = item_id("https://www.example.com/a/")
    assert a == b and a.startswith("aw-") and len(a) == 11


def test_normalize_merges_same_url_across_sources():
    dt = datetime(2026, 8, 22, tzinfo=timezone.utc)
    raws = [
        RawItem(source="hn", url="https://example.com/post", title="Post", excerpt="short",
                published_at=dt, metrics={"points": 120}),
        RawItem(source="reddit-claudeai", url="https://www.example.com/post/?utm_source=r", title="Post",
                excerpt="a much longer excerpt of the same post", metrics={"points": 30}),
    ]
    items = normalize(raws, {"hn": "en", "reddit-claudeai": "en"})
    assert len(items) == 1
    it = items[0]
    assert it.mentions == ["hn", "reddit-claudeai"]
    assert it.metrics == {"points": 120}                 # 同キーは max
    assert it.excerpt.startswith("a much longer")         # 長い方を採用
    assert it.published_at == dt
    assert it.group == "en" and it.source == "hn"


def test_normalize_merges_near_identical_titles():
    raws = [
        RawItem(source="zenn-llm", url="https://zenn.dev/a/articles/1", title="Claude Code の hooks を試してみた"),
        RawItem(source="hatena-it-hot", url="https://b.hatena.ne.jp/entry/s/zenn.dev/a/articles/1",
                title="Claude Code の hooks を試してみた - Zenn"),
    ]
    items = normalize(raws, {})
    assert len(items) == 1
    assert items[0].mentions == ["zenn-llm", "hatena-it-hot"]


def test_normalize_keeps_distinct_items():
    raws = [RawItem(source="s", url="https://a.com/1", title="One"),
            RawItem(source="s", url="https://a.com/2", title="Two")]
    assert len(normalize(raws, {"s": "misc"})) == 2


def test_normalize_keeps_patch_releases_distinct():
    raws = [RawItem(source="codex-releases", url="https://github.com/openai/codex/releases/tag/rust-v0.50.1", title="Codex rust-v0.50.1"),
            RawItem(source="codex-releases", url="https://github.com/openai/codex/releases/tag/rust-v0.50.2", title="Codex rust-v0.50.2")]
    assert len(normalize(raws, {"codex-releases": "official"})) == 2
