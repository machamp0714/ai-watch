from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .r2_sync import (
    AwsCli,
    R2Config,
    R2SyncError,
    create_watchlist,
    pull,
    push_results,
    update_watchlist,
)


def cmd_pull(args: argparse.Namespace, aws: AwsCli) -> None:
    pull(args.root, aws)
    print("R2からの取得に成功しました")


def cmd_push_results(args: argparse.Namespace, aws: AwsCli) -> None:
    push_results(args.root, aws)
    print("R2への成果物保存に成功しました")


def cmd_create_watchlist(args: argparse.Namespace, aws: AwsCli) -> None:
    create_watchlist(args.root, args.file, args.sources, aws)
    print("R2への監視設定の初回登録に成功しました")


def cmd_update_watchlist(args: argparse.Namespace, aws: AwsCli) -> None:
    update_watchlist(args.root, args.file, args.sources, aws)
    print("R2の監視設定更新に成功しました")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-watch-r2",
        description="非公開R2とai-watchの実行データを安全に同期します",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    pull_parser = subcommands.add_parser("pull", help="設定と実行データを取得")
    pull_parser.add_argument("--root", type=Path, required=True)
    pull_parser.set_defaults(func=cmd_pull)

    push_parser = subcommands.add_parser(
        "push-results", help="実行成果物だけを保存"
    )
    push_parser.add_argument("--root", type=Path, required=True)
    push_parser.set_defaults(func=cmd_push_results)

    for name, help_text, func in (
        (
            "create-watchlist",
            "監視設定を初回登録",
            cmd_create_watchlist,
        ),
        (
            "update-watchlist",
            "取得時のETagを使って監視設定を更新",
            cmd_update_watchlist,
        ),
    ):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--file", type=Path, required=True)
        command.add_argument("--sources", type=Path, required=True)
        command.set_defaults(func=func)

    return parser


def dispatch(args: argparse.Namespace, aws: AwsCli) -> None:
    args.func(args, aws)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = R2Config.from_env(os.environ)
        aws = AwsCli(config, runner=subprocess.run, environ=os.environ)
        aws.check_v2()
        dispatch(args, aws)
        return 0
    except R2SyncError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
