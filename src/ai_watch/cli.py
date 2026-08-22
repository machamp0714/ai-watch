from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .adapters.base import FetchContext, TimeWindow, make_client
from .claude_runner import ClaudeRunner
from .collect import collect
from .config import Settings, load_settings
from .decisions import DecisionStore, sync_decisions
from .doctor import cmd_doctor
from .pipeline import STAGES, run_nightly, today_jst
from .vault import Vault, init_vault

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_config() -> Path:
    return Path(os.environ.get("AI_WATCH_CONFIG") or REPO_ROOT / "sources.yaml")


def _day(s: str | None) -> date:
    return date.fromisoformat(s) if s else today_jst()


def _ctx(settings: Settings, with_runner: bool) -> FetchContext:
    runner = ClaudeRunner(settings.claude_bin, settings.model, cwd=settings.root) if with_runner else None
    return FetchContext(http=make_client(settings.user_agent), data_dir=settings.data_dir, root=settings.root, runner=runner)


def cmd_nightly(settings: Settings, args: argparse.Namespace) -> int:
    if args.from_stage not in STAGES:
        print(f"unknown stage '{args.from_stage}'. choose from: {', '.join(STAGES)}", file=sys.stderr)
        return 2
    report = run_nightly(settings, _day(args.date), from_stage=args.from_stage, dry_run=args.dry_run)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=1))
    return 0


def cmd_collect(settings: Settings, args: argparse.Namespace) -> int:
    window = TimeWindow.ending_at(datetime.now(timezone.utc), settings.window_hours)
    only = {args.only} if args.only else None
    res = collect(settings, window, _ctx(settings, with_runner=False), day=_day(args.date), only_ids=only)
    print(json.dumps({"counts": res.counts, "warnings": res.warnings}, ensure_ascii=False, indent=1))
    return 0 if not res.warnings else 1


def cmd_x_collect(settings: Settings, args: argparse.Namespace) -> int:
    window = TimeWindow.ending_at(datetime.now(timezone.utc), settings.window_hours)
    res = collect(settings, window, _ctx(settings, with_runner=True), day=_day(args.date),
                  types={"x_mcp"}, exclude_types=set())
    print(json.dumps({"counts": res.counts, "warnings": res.warnings,
                      "titles": [i.title for i in res.items]}, ensure_ascii=False, indent=1))
    return 0 if not res.warnings else 1


def cmd_sync(settings: Settings, args: argparse.Namespace) -> int:
    vault = Vault(settings.vault_dir)
    store = DecisionStore(settings.data_dir / "decisions.jsonl")
    added = sync_decisions(vault, store, today_jst(), include_today=True)
    print(f"added {len(added)} decision(s)")
    for d in added:
        print(f"- [{d.decision}] {d.title}")
    return 0


def cmd_init_vault(settings: Settings, args: argparse.Namespace) -> int:
    # テンプレートはコードの一部なのでリポジトリ直下から読む（settings.root は sources.yaml の場所）
    created = init_vault(Vault(settings.vault_dir), REPO_ROOT / "templates" / "vault")
    print(f"created {len(created)} path(s) under {settings.vault_dir}")
    for p in created:
        print(f"- {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ai-watch", description="LLM 情報の夜間収集・トリアージ・ダイジェスト生成")
    p.add_argument("--config", type=Path, default=None, help="sources.yaml のパス（既定: $AI_WATCH_CONFIG → リポジトリ直下）")
    sub = p.add_subparsers(dest="command", required=True)

    n = sub.add_parser("nightly", help="collect → x-collect → sync → triage → render を通しで実行")
    n.add_argument("--date", help="YYYY-MM-DD（既定: 今日 JST）")
    n.add_argument("--from", dest="from_stage", default="collect", help=f"途中から再実行: {', '.join(STAGES)}")
    n.add_argument("--dry-run", action="store_true", help="vault に書かず data/work/ にダイジェストを出す")
    n.set_defaults(func=cmd_nightly)

    c = sub.add_parser("collect", help="X 以外のソースを取得して件数と失敗を表示")
    c.add_argument("--date")
    c.add_argument("--only", help="ソース id を 1 つに絞る")
    c.set_defaults(func=cmd_collect)

    x = sub.add_parser("x-collect", help="X（Playwright MCP）だけ取得")
    x.add_argument("--date")
    x.set_defaults(func=cmd_x_collect)

    sub.add_parser("sync", help="ダイジェストのチェックを今すぐ backlog / outputs に反映").set_defaults(func=cmd_sync)
    sub.add_parser("init-vault", help="vault に初期ファイルを作る（既存は触らない）").set_defaults(func=cmd_init_vault)
    sub.add_parser("doctor", help="実行環境の事前チェック").set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.config or default_config())
    return int(args.func(settings, args))


if __name__ == "__main__":
    sys.exit(main())
