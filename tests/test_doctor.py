from pathlib import Path

from ai_watch.config import Settings, SourceConfig
from ai_watch.doctor import run_checks

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_run_checks_reports_each_item(tmp_path):
    s = Settings(vault_dir=tmp_path / "vault", data_dir=tmp_path / "data", window_hours=30, user_agent="t",
                 claude_bin=str(tmp_path / "nope-claude"), npx_bin=str(tmp_path / "nope-npx"), model="sonnet", root=REPO_ROOT,
                 sources=[SourceConfig(id="a", type="rss", group="en", params={"url": "https://x"}),
                          SourceConfig(id="b", type="unknown_type", group="en", params={})])
    results = dict((name, (ok, detail)) for name, ok, detail in run_checks(s))
    assert results["claude_bin"][0] is False
    assert results["npx_bin"][0] is False
    assert results["vault_dir"][0] is False and "init-vault" in results["vault_dir"][1]
    assert results["adapters"][0] is False and "unknown_type" in results["adapters"][1]
    assert results["prompts"][0] is True          # 本物の prompts/ schemas/ は揃っている
    assert results["data_dir"][0] is True          # 無ければ作れる
