from pathlib import Path

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
