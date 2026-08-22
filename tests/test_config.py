from pathlib import Path

from ai_watch.config import load_settings


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
    s = load_settings(p)
    assert s.vault_dir == tmp_path / "v"
    assert s.data_dir == tmp_path / "d"
