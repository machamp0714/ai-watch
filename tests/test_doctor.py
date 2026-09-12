import argparse
from pathlib import Path

from ai_watch.config import Settings, SourceConfig
from ai_watch.doctor import cmd_doctor, run_checks

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
    assert results["playwright_profile"][0] is True and "not required" in results["playwright_profile"][1]


def test_claude_version_failure_is_ng(tmp_path):
    # fake claude script that exit 1
    fake_claude = tmp_path / "fake-claude"
    fake_claude.write_text("#!/bin/sh\nexit 1\n")
    fake_claude.chmod(0o755)

    s = Settings(vault_dir=tmp_path / "vault", data_dir=tmp_path / "data", window_hours=30, user_agent="t",
                 claude_bin=str(fake_claude), npx_bin=str(tmp_path / "nope-npx"), model="sonnet", root=REPO_ROOT,
                 sources=[SourceConfig(id="a", type="rss", group="en", params={"url": "https://x"})])
    results = dict((name, (ok, detail)) for name, ok, detail in run_checks(s))
    assert results["claude_bin"][0] is False
    assert "rc=1" in results["claude_bin"][1]


def test_cmd_doctor_exit_code(tmp_path, capsys):
    # NG settings (bad claude_bin and unknown_type source)
    s = Settings(vault_dir=tmp_path / "vault", data_dir=tmp_path / "data", window_hours=30, user_agent="t",
                 claude_bin=str(tmp_path / "nope-claude"), npx_bin=str(tmp_path / "nope-npx"), model="sonnet", root=REPO_ROOT,
                 sources=[SourceConfig(id="b", type="unknown_type", group="en", params={})])
    ret = cmd_doctor(s, argparse.Namespace())
    assert ret == 1
    captured = capsys.readouterr()
    assert "NG" in captured.out


def test_ci_mode_checks_auth_and_r2_names_without_values(tmp_path):
    secret = "test-secret-must-not-appear"
    settings = Settings(
        vault_dir=tmp_path / "vault",
        data_dir=tmp_path / "data",
        window_hours=30,
        user_agent="t",
        claude_bin=str(tmp_path / "nope-claude"),
        npx_bin=str(tmp_path / "nope-npx"),
        model="sonnet",
        root=REPO_ROOT,
        sources=[SourceConfig(id="x", type="x_mcp", group="x", params={})],
    )
    environment = {
        "ANTHROPIC_API_KEY": secret,
        "R2_ACCESS_KEY_ID": "test-access",
        "R2_SECRET_ACCESS_KEY": secret,
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "R2_BUCKET_NAME": "example-private-bucket",
    }

    results = dict(
        (name, (ok, detail))
        for name, ok, detail in run_checks(settings, environ=environment, ci=True)
    )

    assert results["anthropic_api_key"] == (True, "設定済み")
    assert results["r2_environment"] == (True, "必要な4変数を設定済み")
    assert results["npx_bin"] == (True, "CIでは不要（x_collectを省略）")
    assert results["playwright_profile"] == (
        True,
        "CIでは不要（x_collectを省略）",
    )
    assert secret not in repr(results)


def test_missing_ci_credentials_reports_names_only(tmp_path):
    settings = Settings(
        vault_dir=tmp_path / "vault",
        data_dir=tmp_path / "data",
        window_hours=30,
        user_agent="t",
        claude_bin=str(tmp_path / "nope-claude"),
        npx_bin=str(tmp_path / "nope-npx"),
        model="sonnet",
        root=REPO_ROOT,
        sources=[],
    )

    results = dict(
        (name, (ok, detail))
        for name, ok, detail in run_checks(settings, environ={}, ci=True)
    )

    assert results["anthropic_api_key"] == (False, "ANTHROPIC_API_KEYが未設定")
    assert results["r2_environment"][0] is False
    assert "R2_ACCESS_KEY_ID" in results["r2_environment"][1]
    assert "R2_SECRET_ACCESS_KEY" in results["r2_environment"][1]


def test_worker_access_redirect_is_reachable(tmp_path):
    settings = Settings(
        vault_dir=tmp_path / "vault",
        data_dir=tmp_path / "data",
        window_hours=30,
        user_agent="t",
        claude_bin=str(tmp_path / "nope-claude"),
        npx_bin=str(tmp_path / "nope-npx"),
        model="sonnet",
        root=REPO_ROOT,
        sources=[],
    )

    class Response:
        status_code = 302
        headers = {"location": "https://example.cloudflareaccess.com/login"}

    results = dict(
        (name, (ok, detail))
        for name, ok, detail in run_checks(
            settings,
            environ={"AI_WATCH_WORKER_URL": "https://ai-watch.example.workers.dev"},
            http_get=lambda *args, **kwargs: Response(),
        )
    )

    assert results["worker"] == (True, "Cloudflare Accessで保護されています")
