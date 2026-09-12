import json
import subprocess
from pathlib import Path

import pytest

from ai_watch.r2_sync import (
    AwsCli,
    R2Config,
    R2ConflictError,
    R2InputError,
    R2SyncError,
    _pull_watchlist,
    create_watchlist,
    update_watchlist,
)


VALID_ENV = {
    "R2_ACCESS_KEY_ID": "test-access-marker",
    "R2_SECRET_ACCESS_KEY": "test-secret-marker",
    "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    "R2_BUCKET_NAME": "example-private-bucket",
}


def completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def make_aws(runner):
    return AwsCli(
        R2Config.from_env(VALID_ENV),
        runner=runner,
        environ={"PATH": "/usr/bin:/bin"},
    )


@pytest.mark.parametrize("missing", tuple(VALID_ENV))
def test_r2_config_rejects_missing_required_value(missing):
    env = {key: value for key, value in VALID_ENV.items() if key != missing}
    with pytest.raises(R2InputError, match=missing):
        R2Config.from_env(env)


@pytest.mark.parametrize("empty", tuple(VALID_ENV))
def test_r2_config_rejects_empty_required_value(empty):
    with pytest.raises(R2InputError, match=empty):
        R2Config.from_env(VALID_ENV | {empty: ""})


@pytest.mark.parametrize("account_id", ["a" * 31, "g" * 32, "A" * 32])
def test_r2_config_rejects_invalid_account_id(account_id):
    with pytest.raises(R2InputError, match="CLOUDFLARE_ACCOUNT_ID"):
        R2Config.from_env(VALID_ENV | {"CLOUDFLARE_ACCOUNT_ID": account_id})


@pytest.mark.parametrize("bucket", ["ab", "Uppercase", "under_score", "a" * 64])
def test_r2_config_rejects_invalid_bucket_name(bucket):
    with pytest.raises(R2InputError, match="R2_BUCKET_NAME"):
        R2Config.from_env(VALID_ENV | {"R2_BUCKET_NAME": bucket})


def test_r2_config_builds_endpoint_without_exposing_credentials():
    config = R2Config.from_env(VALID_ENV)
    assert config.endpoint_url == f"https://{'a' * 32}.r2.cloudflarestorage.com"
    assert "test-access-marker" not in repr(config)
    assert "test-secret-marker" not in repr(config)


def test_aws_cli_uses_argument_array_and_child_environment_only():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return completed(argv, stdout='{"ETag":"\\"etag-a\\""}')

    config = R2Config.from_env(VALID_ENV)
    aws = AwsCli(config, runner=runner, environ={"PATH": "/bin"})
    result = aws.run_json(
        "監視設定の確認",
        [
            "s3api", "head-object",
            "--bucket", config.bucket_name,
            "--key", "config/watchlist.yaml",
            "--output", "json",
        ],
    )

    argv, kwargs = calls[0]
    assert isinstance(argv, list)
    assert argv[:7] == [
        "aws", "--no-cli-pager", "--region", "auto", "--endpoint-url",
        config.endpoint_url, "s3api",
    ]
    assert "test-access-marker" not in argv
    assert "test-secret-marker" not in argv
    assert "shell" not in kwargs
    assert kwargs["env"]["AWS_ACCESS_KEY_ID"] == "test-access-marker"
    assert kwargs["env"]["AWS_SECRET_ACCESS_KEY"] == "test-secret-marker"
    assert kwargs["env"]["AWS_DEFAULT_REGION"] == "auto"
    assert kwargs["env"]["AWS_REGION"] == "auto"
    assert kwargs["env"]["AWS_EC2_METADATA_DISABLED"] == "true"
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False
    assert result == {"ETag": '"etag-a"'}


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "An error occurred (AccessDenied) when calling private-interest "
            "test-secret-marker",
            "AccessDenied",
        ),
        ("private-interest test-secret-marker", "不明"),
    ],
)
def test_aws_error_is_sanitized(stderr, expected):
    def runner(argv, **kwargs):
        return completed(argv, returncode=255, stderr=stderr)

    aws = make_aws(runner)
    with pytest.raises(R2SyncError) as captured:
        aws.run("成果物の保存", ["s3", "sync", "local", "remote"])
    message = str(captured.value)
    assert expected in message
    assert "成果物の保存" in message and "255" in message
    assert "private-interest" not in message
    assert "test-secret-marker" not in message


def test_precondition_failed_is_conflict():
    def runner(argv, **kwargs):
        return completed(
            argv,
            returncode=255,
            stderr="An error occurred (PreconditionFailed) when calling PutObject",
        )

    with pytest.raises(R2ConflictError):
        make_aws(runner).run("監視設定の更新", ["s3api", "put-object"])


def test_missing_aws_cli_is_input_error_without_path():
    def runner(argv, **kwargs):
        raise FileNotFoundError("/private/bin/aws")

    with pytest.raises(R2InputError) as captured:
        make_aws(runner).check_v2()
    assert "/private/bin/aws" not in str(captured.value)


@pytest.mark.parametrize("version", ["aws-cli/1.42.0", "not-aws"])
def test_aws_cli_v2_is_required(version):
    def runner(argv, **kwargs):
        return completed(argv, stdout=version)

    with pytest.raises(R2InputError, match="AWS CLI v2"):
        make_aws(runner).check_v2()


def test_invalid_json_success_is_sync_error_without_body():
    def runner(argv, **kwargs):
        return completed(argv, stdout="private-interest test-secret-marker")

    with pytest.raises(R2SyncError) as captured:
        make_aws(runner).run_json("監視設定の確認", ["s3api", "head-object"])
    message = str(captured.value)
    assert "private-interest" not in message
    assert "test-secret-marker" not in message


def test_pull_watchlist_uses_head_etag_and_replaces_atomically(tmp_path):
    root = tmp_path / "runtime"
    target = root / "config/watchlist.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("old-local", encoding="utf-8")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if "head-object" in argv:
            return completed(argv, stdout='{"ETag":"\\"etag-a\\""}')
        if "get-object" in argv:
            assert argv[argv.index("--if-match") + 1] == '"etag-a"'
            output = Path(argv[-1])
            assert output != target and output.parent == target.parent
            output.write_text("version: 1\ntools: []\n", encoding="utf-8")
            return completed(argv, stdout="{}")
        raise AssertionError("想定外のAWS操作です")

    _pull_watchlist(root, make_aws(runner))

    assert ["head-object" in call for call in calls] == [True, False]
    assert "get-object" in calls[1]
    assert target.read_text(encoding="utf-8") == "version: 1\ntools: []\n"
    assert (root / ".r2-state/watchlist-etag").read_text(encoding="utf-8") == '"etag-a"\n'


def test_pull_watchlist_failure_keeps_existing_file_and_state(tmp_path):
    root = tmp_path / "runtime"
    target = root / "config/watchlist.yaml"
    state = root / ".r2-state/watchlist-etag"
    target.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    target.write_text("old-local", encoding="utf-8")
    state.write_text('"etag-old"\n', encoding="utf-8")

    def runner(argv, **kwargs):
        if "head-object" in argv:
            return completed(argv, stdout='{"ETag":"\\"etag-a\\""}')
        if "get-object" in argv:
            Path(argv[-1]).write_text("partial-private", encoding="utf-8")
            return completed(
                argv,
                returncode=255,
                stderr="AccessDenied partial-private test-secret-marker",
            )
        raise AssertionError("設定取得失敗後に別のAWS操作へ進んではいけません")

    with pytest.raises(R2SyncError):
        _pull_watchlist(root, make_aws(runner))

    assert target.read_text(encoding="utf-8") == "old-local"
    assert state.read_text(encoding="utf-8") == '"etag-old"\n'
    assert list(target.parent.glob(".watchlist.yaml.*")) == []


def write_example_watchlist_and_sources(tmp_path):
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "sources:\n  - id: example-feed\n    type: rss\n",
        encoding="utf-8",
    )
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text(
        "version: 1\ntools:\n"
        "  - id: example-tool\n"
        "    name: サンプル対象\n"
        "    enabled: true\n"
        "    focus: [サンプル更新]\n"
        "    source_ids: [example-feed]\n",
        encoding="utf-8",
    )
    return watchlist, sources


def json_runner(calls, payload):
    def runner(argv, **kwargs):
        calls.append(argv)
        return completed(argv, stdout=json.dumps(payload))

    return runner


def write_etag(root, value):
    state = root / ".r2-state/watchlist-etag"
    state.parent.mkdir(parents=True)
    state.write_text(value + "\n", encoding="utf-8")


def test_create_watchlist_uses_if_none_match_and_saves_returned_etag(tmp_path):
    watchlist, sources = write_example_watchlist_and_sources(tmp_path)
    calls = []
    aws = make_aws(json_runner(calls, {"ETag": '"etag-created"'}))

    create_watchlist(tmp_path / "runtime", watchlist, sources, aws)

    argv = calls[0]
    assert argv[argv.index("--if-none-match") + 1] == "*"
    assert argv[argv.index("--body") + 1] == str(watchlist)
    assert (
        tmp_path / "runtime/.r2-state/watchlist-etag"
    ).read_text(encoding="utf-8").strip() == '"etag-created"'


def test_update_watchlist_uses_saved_if_match(tmp_path):
    root = tmp_path / "runtime"
    write_etag(root, '"etag-old"')
    watchlist, sources = write_example_watchlist_and_sources(tmp_path)
    calls = []

    update_watchlist(
        root,
        watchlist,
        sources,
        make_aws(json_runner(calls, {"ETag": '"etag-new"'})),
    )

    argv = calls[0]
    assert argv[argv.index("--if-match") + 1] == '"etag-old"'
    assert "--if-none-match" not in argv
    assert (root / ".r2-state/watchlist-etag").read_text().strip() == '"etag-new"'


def test_update_watchlist_requires_saved_etag_before_aws_call(tmp_path):
    watchlist, sources = write_example_watchlist_and_sources(tmp_path)
    calls = []
    with pytest.raises(R2InputError, match="pull"):
        update_watchlist(
            tmp_path / "runtime",
            watchlist,
            sources,
            make_aws(json_runner(calls, {"ETag": '"unexpected"'})),
        )
    assert calls == []


@pytest.mark.parametrize(
    "document",
    [
        "version: 1\ntools: [private-value\n",
        "version: 1\ntools:\n  - id: example\n    name: Example\n"
        "    enabled: true\n    focus: []\n    source_ids: [missing]\n",
    ],
)
def test_create_watchlist_validates_before_aws_call(tmp_path, document):
    watchlist, sources = write_example_watchlist_and_sources(tmp_path)
    watchlist.write_text(document, encoding="utf-8")
    calls = []
    with pytest.raises(R2InputError) as captured:
        create_watchlist(
            tmp_path / "runtime",
            watchlist,
            sources,
            make_aws(json_runner(calls, {"ETag": '"unexpected"'})),
        )
    assert calls == []
    assert "private-value" not in str(captured.value)


def test_create_watchlist_conflict_does_not_change_saved_etag(tmp_path):
    root = tmp_path / "runtime"
    write_etag(root, '"etag-old"')
    watchlist, sources = write_example_watchlist_and_sources(tmp_path)

    def runner(argv, **kwargs):
        return completed(
            argv,
            returncode=255,
            stderr="An error occurred (PreconditionFailed) when calling PutObject",
        )

    with pytest.raises(R2ConflictError):
        create_watchlist(root, watchlist, sources, make_aws(runner))
    assert (root / ".r2-state/watchlist-etag").read_text().strip() == '"etag-old"'


@pytest.mark.parametrize("payload", [{}, {"ETag": ""}, {"ETag": 123}])
def test_watchlist_operations_reject_missing_response_etag(tmp_path, payload):
    watchlist, sources = write_example_watchlist_and_sources(tmp_path)
    with pytest.raises(R2SyncError, match="ETag"):
        create_watchlist(
            tmp_path / "runtime",
            watchlist,
            sources,
            make_aws(json_runner([], payload)),
        )
