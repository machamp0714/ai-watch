from pathlib import Path

import pytest

from ai_watch.config import load_settings, load_source_ids
from ai_watch.watchlist import WatchlistError, load_watchlist


YAML = """
defaults:
  vault_dir: /tmp/ai-watch-vault
  data_dir: ./data
  window_hours: 30
  user_agent: "ai-watch/test"
  claude_bin: claude
  npx_bin: ~/.nodenv/shims/npx
  model: sonnet
sources:
  - id: zenn-claudecode
    type: rss
    group: jp
    url: https://zenn.dev/topics/claudecode/feed
    lang: ja
  - id: hn
    type: hn_algolia
    group: en
    queries: [claude, codex]
    min_points: 50
"""


def test_load_settings(tmp_path: Path, monkeypatch):
    p = tmp_path / "sources.yaml"
    p.write_text(YAML)
    monkeypatch.delenv("AI_WATCH_VAULT_DIR", raising=False)
    monkeypatch.delenv("AI_WATCH_DATA_DIR", raising=False)
    s = load_settings(p)
    assert s.vault_dir == Path("/tmp/ai-watch-vault")
    assert s.data_dir == tmp_path / "data"          # 相対パスは sources.yaml の場所基準
    assert s.window_hours == 30
    assert s.npx_bin.startswith("/")                 # ~ が展開される
    assert s.claude_bin == "claude"
    assert s.root == tmp_path
    assert [x.id for x in s.sources] == ["zenn-claudecode", "hn"]
    assert s.sources[0].type == "rss"
    assert s.sources[0].group == "jp"
    assert s.sources[0].params == {"url": "https://zenn.dev/topics/claudecode/feed", "lang": "ja"}
    assert s.sources[1].params["queries"] == ["claude", "codex"]


def test_env_override(tmp_path: Path, monkeypatch):
    p = tmp_path / "sources.yaml"
    p.write_text(YAML)
    monkeypatch.setenv("AI_WATCH_VAULT_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("AI_WATCH_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("AI_WATCH_CHECKS_DIR", str(tmp_path / "c"))
    s = load_settings(p)
    assert s.vault_dir == tmp_path / "v"
    assert s.data_dir == tmp_path / "d"
    assert s.checks_dir == tmp_path / "c"


def test_watchlist_environment_overrides_config(tmp_path, monkeypatch):
    config = tmp_path / "sources.yaml"
    config.write_text("defaults:\n  watchlist_file: missing.yaml\nsources: []\n", encoding="utf-8")
    private = tmp_path / "private-watchlist.yaml"
    private.write_text("version: 1\ntools: []\n", encoding="utf-8")
    monkeypatch.setenv("AI_WATCH_WATCHLIST_FILE", str(private))
    assert load_settings(config).watchlist == []


def test_watchlist_relative_path_uses_sources_directory(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text("defaults:\n  watchlist_file: private.yaml\nsources: []\n", encoding="utf-8")
    (tmp_path / "private.yaml").write_text("version: 1\ntools: []\n", encoding="utf-8")
    assert load_settings(config).watchlist == []


@pytest.mark.parametrize("value", ["", "   "])
def test_watchlist_environment_rejects_explicit_empty_value(tmp_path, monkeypatch, value):
    config = tmp_path / "sources.yaml"
    config.write_text("sources: []\n", encoding="utf-8")
    monkeypatch.setenv("AI_WATCH_WATCHLIST_FILE", value)
    with pytest.raises(WatchlistError, match="watchlist_file"):
        load_settings(config)


def test_watchlist_rejects_missing_explicit_file(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text("defaults:\n  watchlist_file: missing.yaml\nsources: []\n", encoding="utf-8")
    with pytest.raises(WatchlistError, match="missing.yaml"):
        load_settings(config)


def test_duplicate_source_ids_are_rejected_when_watchlist_is_enabled(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text("""defaults:
  watchlist_file: watchlist.yaml
sources:
  - id: duplicate
    type: rss
  - id: duplicate
    type: rss
""", encoding="utf-8")
    (tmp_path / "watchlist.yaml").write_text("version: 1\ntools: []\n", encoding="utf-8")
    with pytest.raises(WatchlistError, match="収集先ID"):
        load_settings(config)


def test_missing_watchlist_configuration_keeps_legacy_behavior(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text("sources: []\n", encoding="utf-8")
    assert load_settings(config).watchlist == []


def test_public_watchlist_example_has_no_personal_tools():
    root = Path(__file__).resolve().parents[1]
    assert load_watchlist(root / "config/watchlist.example.yaml", set()) == []
    assert load_settings(root / "sources.yaml").watchlist == []


def test_repository_zenn_sources_enable_api_metrics_and_popularity_recheck():
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "sources.yaml")
    zenn_sources = [source for source in settings.sources if source.id.startswith("zenn-")]

    assert [source.params["topic"] for source in zenn_sources] == [
        "claudecode",
        "codex",
        "llm",
    ]
    assert all(source.type == "zenn" for source in zenn_sources)
    assert all(source.params["recheck_popularity"] is True for source in zenn_sources)


def test_load_source_ids_ignores_ambient_watchlist(tmp_path, monkeypatch):
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "sources:\n  - id: example-feed\n    type: rss\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "AI_WATCH_WATCHLIST_FILE",
        str(tmp_path / "missing-private.yaml"),
    )
    assert load_source_ids(sources) == {"example-feed"}


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("sources: wrong\n", "sources"),
        ("sources:\n  - type: rss\n", r"sources\[1\]\.id"),
        ("sources:\n  - id: duplicate\n  - id: duplicate\n", "重複"),
        ("sources: [private-value\n", "YAML"),
    ],
)
def test_load_source_ids_rejects_invalid_document_without_body(
    tmp_path, document, expected,
):
    sources = tmp_path / "sources.yaml"
    sources.write_text(document, encoding="utf-8")
    with pytest.raises(WatchlistError, match=expected) as captured:
        load_source_ids(sources)
    assert "private-value" not in str(captured.value)


def test_load_source_ids_missing_file_hides_parent_path(tmp_path):
    sources = tmp_path / "private-parent" / "sources.yaml"
    with pytest.raises(WatchlistError) as captured:
        load_source_ids(sources)
    assert "sources.yaml" in str(captured.value)
    assert "private-parent" not in str(captured.value)
