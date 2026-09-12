from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence


class R2SyncError(RuntimeError):
    exit_code = 1


class R2InputError(R2SyncError):
    exit_code = 2


class R2ConflictError(R2SyncError):
    exit_code = 3


_ACCOUNT_ID = re.compile(r"^[0-9a-f]{32}$")
_BUCKET_NAME = re.compile(r"^[a-z0-9-]{3,63}$")
_REQUIRED_ENV = (
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
    "R2_BUCKET_NAME",
)
_ALLOWED_AWS_ERRORS = (
    "AccessDenied",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "NoSuchKey",
    "PreconditionFailed",
)


@dataclass(frozen=True)
class R2Config:
    account_id: str
    bucket_name: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> R2Config:
        values: dict[str, str] = {}
        for name in _REQUIRED_ENV:
            value = environ.get(name)
            if value is None or not value.strip():
                raise R2InputError(f"環境変数{name}を設定してください")
            values[name] = value
        if _ACCOUNT_ID.fullmatch(values["CLOUDFLARE_ACCOUNT_ID"]) is None:
            raise R2InputError("CLOUDFLARE_ACCOUNT_IDの形式が不正です")
        if _BUCKET_NAME.fullmatch(values["R2_BUCKET_NAME"]) is None:
            raise R2InputError("R2_BUCKET_NAMEの形式が不正です")
        return cls(
            account_id=values["CLOUDFLARE_ACCOUNT_ID"],
            bucket_name=values["R2_BUCKET_NAME"],
            access_key_id=values["R2_ACCESS_KEY_ID"],
            secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        )

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


class ProcessRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


class AwsCli:
    def __init__(
        self,
        config: R2Config,
        *,
        runner: ProcessRunner,
        environ: Mapping[str, str],
        executable: str = "aws",
    ) -> None:
        self.config = config
        self._runner = runner
        self._executable = executable
        self._child_env = dict(environ)
        self._child_env.update(
            {
                "AWS_ACCESS_KEY_ID": config.access_key_id,
                "AWS_SECRET_ACCESS_KEY": config.secret_access_key,
                "AWS_DEFAULT_REGION": "auto",
                "AWS_REGION": "auto",
                "AWS_EC2_METADATA_DISABLED": "true",
            }
        )

    def _invoke(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                argv,
                env=self._child_env,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise R2InputError("AWS CLI v2が見つかりません") from exc

    def check_v2(self) -> None:
        result = self._invoke([self._executable, "--version"])
        version = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0 or not version.startswith("aws-cli/2."):
            raise R2InputError("AWS CLI v2を利用できません")

    def run(self, operation: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = [
            self._executable,
            "--no-cli-pager",
            "--region",
            "auto",
            "--endpoint-url",
            self.config.endpoint_url,
            *args,
        ]
        result = self._invoke(argv)
        if result.returncode == 0:
            return result
        error_code = next(
            (code for code in _ALLOWED_AWS_ERRORS if f"({code})" in result.stderr),
            "不明",
        )
        message = (
            f"{operation}に失敗しました"
            f"（AWSエラー: {error_code}、終了コード: {result.returncode}）"
        )
        if error_code == "PreconditionFailed":
            raise R2ConflictError(message)
        raise R2SyncError(message)

    def run_json(self, operation: str, args: Sequence[str]) -> dict[str, object]:
        result = self.run(operation, args)
        try:
            value = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise R2SyncError(f"{operation}の応答を解釈できません") from exc
        if not isinstance(value, dict):
            raise R2SyncError(f"{operation}の応答を解釈できません")
        return value
