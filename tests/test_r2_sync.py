import subprocess

import pytest

from ai_watch.r2_sync import (
    AwsCli,
    R2Config,
    R2ConflictError,
    R2InputError,
    R2SyncError,
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
