from pathlib import Path

import pytest

from ai_watch.r2_cli import build_parser, main
from ai_watch.r2_sync import R2ConflictError, R2InputError, R2SyncError


VALID_ENV = {
    "R2_ACCESS_KEY_ID": "test-access-marker",
    "R2_SECRET_ACCESS_KEY": "test-secret-marker",
    "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    "R2_BUCKET_NAME": "example-private-bucket",
}


def set_valid_env(monkeypatch):
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("command", ["pull", "push-results"])
def test_data_commands_only_accept_root(command):
    args = build_parser().parse_args([command, "--root", "/runtime"])
    assert args.root == Path("/runtime")
    assert not hasattr(args, "file")
    assert not hasattr(args, "sources")


@pytest.mark.parametrize("command", ["create-watchlist", "update-watchlist"])
def test_watchlist_commands_require_explicit_files(command):
    args = build_parser().parse_args(
        [
            command,
            "--root", "/runtime",
            "--file", "/private/watchlist.yaml",
            "--sources", "./sources.yaml",
        ]
    )
    assert args.root == Path("/runtime")
    assert args.file == Path("/private/watchlist.yaml")
    assert args.sources == Path("sources.yaml")


@pytest.mark.parametrize("option", ["--file", "--sources"])
def test_push_results_rejects_watchlist_options(option):
    with pytest.raises(SystemExit) as captured:
        build_parser().parse_args(
            ["push-results", "--root", "/runtime", option, "/private/value"]
        )
    assert captured.value.code == 2


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            R2SyncError(
                "R2操作に失敗しました（AWSエラー: AccessDenied、終了コード: 255）"
            ),
            1,
        ),
        (R2InputError("入力を検証できません"), 2),
        (R2ConflictError("監視設定が競合しました"), 3),
    ],
)
def test_main_maps_safe_errors_to_exit_codes(
    monkeypatch, capsys, error, expected_code,
):
    set_valid_env(monkeypatch)
    monkeypatch.setattr("ai_watch.r2_cli.AwsCli.check_v2", lambda self: None)
    monkeypatch.setattr(
        "ai_watch.r2_cli.dispatch",
        lambda *args: (_ for _ in ()).throw(error),
    )

    assert main(["pull", "--root", "/runtime"]) == expected_code

    captured = capsys.readouterr()
    assert str(error) in captured.err
    assert "test-secret-marker" not in captured.out + captured.err
    assert "private-interest" not in captured.out + captured.err


def test_main_maps_missing_environment_to_input_error(monkeypatch, capsys):
    assert main(["pull", "--root", "/runtime"]) == 2
    captured = capsys.readouterr()
    assert "R2_ACCESS_KEY_ID" in captured.err


def test_pull_dispatch_does_not_load_ai_watch_settings(
    tmp_path, monkeypatch, capsys,
):
    set_valid_env(monkeypatch)
    calls = []
    monkeypatch.setattr("ai_watch.r2_cli.AwsCli.check_v2", lambda self: None)
    monkeypatch.setattr(
        "ai_watch.r2_cli.pull",
        lambda root, aws: calls.append((root, aws.config.bucket_name)),
    )

    assert main(["pull", "--root", str(tmp_path / "runtime")]) == 0
    assert calls == [(tmp_path / "runtime", "example-private-bucket")]
    assert "成功" in capsys.readouterr().out


def test_help_lists_only_public_commands(capsys):
    with pytest.raises(SystemExit) as captured:
        build_parser().parse_args(["--help"])
    assert captured.value.code == 0
    output = capsys.readouterr().out
    assert "pull" in output
    assert "push-results" in output
    assert "create-watchlist" in output
    assert "update-watchlist" in output
