import json
from pathlib import Path
from types import SimpleNamespace

from ai_watch.cli import main
from ai_watch.pipeline import today_jst
from ai_watch.vault import Vault

YAML = """
defaults:
  vault_dir: {vault}
  data_dir: {data}
sources: []
"""


def _cfg(tmp_path: Path) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(YAML.format(vault=tmp_path / "vault", data=tmp_path / "data"))
    return p


def test_init_vault_then_sync(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["--config", str(cfg), "init-vault"]) == 0
    assert (tmp_path / "vault" / "profile.md").exists() and (tmp_path / "vault" / "digests").is_dir()
    assert "created" in capsys.readouterr().out
    assert main(["--config", str(cfg), "sync"]) == 0
    assert "added 0" in capsys.readouterr().out


def test_nightly_rejects_unknown_stage(tmp_path):
    cfg = _cfg(tmp_path)
    assert main(["--config", str(cfg), "nightly", "--from", "nope"]) == 2


def test_nightly_passes_skip_x_collect_to_pipeline(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_nightly(settings, day, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda: {"mode": "triaged"})

    monkeypatch.setattr("ai_watch.cli.run_nightly", fake_run_nightly)

    assert (
        main(["--config", str(_cfg(tmp_path)), "nightly", "--skip-x-collect"])
        == 0
    )
    assert captured["skip_x_collect"] is True
    assert json.loads(capsys.readouterr().out) == {"mode": "triaged"}


def test_sync_collects_todays_checks(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["--config", str(cfg), "init-vault"]) == 0
    capsys.readouterr()
    vault = Vault(tmp_path / "vault")
    today = today_jst()
    vault.write_atomic(
        vault.digest_path(today),
        "- [x] 🧪 **T** — r ([hn](https://e/1)) ^aw-00000001\n",
    )
    assert main(["--config", str(cfg), "sync"]) == 0
    out = capsys.readouterr().out
    assert "added 1" in out
    assert "^aw-00000001" in vault.backlog.read_text()


def test_invalid_watchlist_returns_configuration_error_without_private_value(tmp_path, capsys):
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text("version: 1\ntools: [private-interest\n", encoding="utf-8")
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(f"defaults:\n  watchlist_file: {watchlist}\nsources: []\n", encoding="utf-8")

    assert main(["--config", str(cfg), "sync"]) == 2

    captured = capsys.readouterr()
    assert "設定エラー" in captured.err
    assert "private-interest" not in captured.err


def test_watchlist_command_is_read_only_and_shows_configuration_states(tmp_path, capsys, monkeypatch):
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text("""version: 1
tools:
  - id: active
    name: 有効対象
    enabled: true
    focus: [更新]
    source_ids: [feed]
  - id: pending
    name: 設定待ち対象
    enabled: true
    focus: []
    source_ids: []
  - id: disabled
    name: 停止対象
    enabled: false
    focus: []
    source_ids: [feed]
""", encoding="utf-8")
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(f"""defaults:
  vault_dir: {tmp_path / 'vault'}
  data_dir: {tmp_path / 'data'}
sources:
  - id: feed
    type: rss
""", encoding="utf-8")
    monkeypatch.setenv("AI_WATCH_WATCHLIST_FILE", str(watchlist))

    def unexpected_call(*args, **kwargs):
        raise AssertionError("外部クライアントを作成してはいけません")

    monkeypatch.setattr("ai_watch.cli.make_client", unexpected_call)
    monkeypatch.setattr("ai_watch.cli.ClaudeRunner", unexpected_call)

    assert main(["--config", str(cfg), "watchlist"]) == 0

    rows = json.loads(capsys.readouterr().out)
    assert [row["status"] for row in rows] == [
        "有効（取得結果は別途確認）", "設定待ち", "停止中",
    ]
    assert not (tmp_path / "vault").exists()
    assert not (tmp_path / "data").exists()
