from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .config import load_source_ids
from .watchlist import WatchlistError, load_watchlist


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
WATCHLIST_KEY = "config/watchlist.yaml"
WATCHLIST_PATH = Path("config/watchlist.yaml")
ETAG_STATE_PATH = Path(".r2-state/watchlist-etag")


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


def _response_etag(payload: Mapping[str, object], operation: str) -> str:
    etag = payload.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise R2SyncError(f"{operation}の応答にETagがありません")
    return etag


def _atomic_write_text(path: Path, value: str, label: str) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    except (OSError, UnicodeError) as exc:
        raise R2InputError(f"{label}を保存できません") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_etag(root: Path, etag: str) -> None:
    _atomic_write_text(root / ETAG_STATE_PATH, etag + "\n", "ETag状態")


def _read_etag(root: Path) -> str:
    try:
        etag = (root / ETAG_STATE_PATH).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise R2InputError("保存済みETagがありません。先にpullしてください") from exc
    if not etag:
        raise R2InputError("保存済みETagがありません。先にpullしてください")
    return etag


def _replace_from_r2(root: Path, aws: AwsCli, etag: str) -> None:
    target = root / WATCHLIST_PATH
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        aws.run(
            "監視設定の取得",
            [
                "s3api",
                "get-object",
                "--bucket",
                aws.config.bucket_name,
                "--key",
                WATCHLIST_KEY,
                "--if-match",
                etag,
                str(temporary),
            ],
        )
        os.replace(temporary, target)
    except R2SyncError:
        raise
    except OSError as exc:
        raise R2InputError("監視設定を保存できません") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _pull_watchlist(root: Path, aws: AwsCli) -> None:
    response = aws.run_json(
        "監視設定の確認",
        [
            "s3api",
            "head-object",
            "--bucket",
            aws.config.bucket_name,
            "--key",
            WATCHLIST_KEY,
            "--output",
            "json",
        ],
    )
    etag = _response_etag(response, "監視設定の確認")
    _replace_from_r2(root, aws, etag)
    _write_etag(root, etag)


def _validate_watchlist(watchlist_file: Path, sources_file: Path) -> None:
    try:
        source_ids = load_source_ids(sources_file)
        load_watchlist(watchlist_file, source_ids)
    except WatchlistError as exc:
        raise R2InputError(f"監視設定を検証できません: {exc}") from exc


def _put_watchlist(
    root: Path,
    watchlist_file: Path,
    sources_file: Path,
    aws: AwsCli,
    *,
    condition_name: str,
    condition_value: str,
) -> None:
    _validate_watchlist(watchlist_file, sources_file)
    response = aws.run_json(
        "監視設定の保存",
        [
            "s3api",
            "put-object",
            "--bucket",
            aws.config.bucket_name,
            "--key",
            WATCHLIST_KEY,
            "--body",
            str(watchlist_file),
            condition_name,
            condition_value,
            "--output",
            "json",
        ],
    )
    _write_etag(root, _response_etag(response, "監視設定の保存"))


def create_watchlist(
    root: Path, watchlist_file: Path, sources_file: Path, aws: AwsCli
) -> None:
    _put_watchlist(
        root,
        watchlist_file,
        sources_file,
        aws,
        condition_name="--if-none-match",
        condition_value="*",
    )


def update_watchlist(
    root: Path, watchlist_file: Path, sources_file: Path, aws: AwsCli
) -> None:
    etag = _read_etag(root)
    _put_watchlist(
        root,
        watchlist_file,
        sources_file,
        aws,
        condition_name="--if-match",
        condition_value=etag,
    )
