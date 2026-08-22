from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .adapters.base import FetchContext, TimeWindow, make_client
from .claude_runner import ClaudeRunner
from .collect import collect
from .config import Settings
from .decisions import DecisionStore, sync_decisions
from .models import Item, raw_from_dict, raw_to_dict
from .normalize import normalize
from .notify import log_line, notify
from .render import render_digest, shown_item_ids
from .seen import SeenStore
from .triage import TriageOutcome, triage
from .vault import Vault

STAGES = ["collect", "x_collect", "sync_decisions", "triage", "render"]
JST = ZoneInfo("Asia/Tokyo")


def today_jst(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(JST).date()


@dataclass
class NightlyReport:
    day: str
    collected: int
    new: int
    shown: int
    mode: str
    warnings: list[str]
    cost_usd: float
    digest_path: str
    decisions_added: int
    n_try: int
    try_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Work:
    """data/work/YYYY-MM-DD/ に段ごとの出力を置き、--from で途中から再実行できるようにする。"""

    def __init__(self, data_dir: Path, day: date):
        self.dir = data_dir / "work" / day.isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, stage: str) -> Path:
        return self.dir / f"{stage}.json"

    def save(self, stage: str, obj: Any) -> None:
        self.path(stage).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    def load(self, stage: str) -> Any | None:
        p = self.path(stage)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


_EMPTY = {"items": [], "warnings": [], "counts": {}}


def run_nightly(
    settings: Settings, day: date, *, from_stage: str = "collect", dry_run: bool = False,
    now: datetime | None = None, runner: Any | None = None, http: Any | None = None,
    notifier: Callable[[str, str], None] = notify,
) -> NightlyReport:
    if from_stage not in STAGES:
        raise ValueError(f"unknown stage '{from_stage}' (choose from {STAGES})")
    start = STAGES.index(from_stage)
    now = now or datetime.now(timezone.utc)
    work = Work(settings.data_dir, day)
    vault = Vault(settings.vault_dir)

    try:
        # --from の前提: スキップする段のうち work ファイルを産む段（render 以外）は
        # 事前に存在していなければならない。無ければ空データにフォールバックして
        # 本物のダイジェストを空で上書きしてしまうため、ここで止める。
        for stage in STAGES[:start]:
            if stage == "render":
                continue
            p = work.path(stage)
            if not p.exists():
                raise FileNotFoundError(
                    f"--from {from_stage}: missing {p}; run nightly without --from first"
                )

        runner = runner or ClaudeRunner(settings.claude_bin, settings.model, cwd=settings.root)
        window = TimeWindow.ending_at(now, settings.window_hours)

        def ctx() -> FetchContext:
            return FetchContext(http=http or make_client(settings.user_agent), data_dir=settings.data_dir,
                                root=settings.root, runner=runner)

        # 1. collect / 2. x_collect ------------------------------------------------
        if start <= STAGES.index("collect"):
            c = collect(settings, window, ctx(), day=day)
            work.save("collect", {"items": [raw_to_dict(i) for i in c.items], "warnings": c.warnings, "counts": c.counts})
        if start <= STAGES.index("x_collect"):
            x = collect(settings, window, ctx(), day=day, types={"x_mcp"}, exclude_types=set())
            work.save("x_collect", {"items": [raw_to_dict(i) for i in x.items], "warnings": x.warnings, "counts": x.counts})
        c_data = work.load("collect") or _EMPTY
        x_data = work.load("x_collect") or _EMPTY
        warnings = list(c_data["warnings"]) + list(x_data["warnings"])
        raws = [raw_from_dict(d) for d in list(c_data["items"]) + list(x_data["items"])]

        # 3. sync_decisions（朝のチェックを回収。triage の few-shot に使うので triage より前） ----
        store = DecisionStore(settings.data_dir / "decisions.jsonl")
        if start <= STAGES.index("sync_decisions"):
            if dry_run:
                added_dicts: list[dict] = []
            else:
                added_dicts = [d.to_dict() for d in sync_decisions(vault, store, day, apply=True)]
            work.save("sync_decisions", {"added": added_dicts})
        decisions_added = len((work.load("sync_decisions") or {"added": []})["added"])

        # 4. triage ----------------------------------------------------------------
        all_items = normalize(raws, settings.source_groups())
        seen_path = settings.data_dir / "seen.sqlite"
        if start <= STAGES.index("triage"):
            if dry_run and not seen_path.exists():
                new_items = list(all_items)
            else:
                with SeenStore(seen_path) as seen:
                    new_items = seen.filter_new(all_items, day)
            outcome = triage(new_items, profile_md=vault.read_profile(), decisions=store.recent(30),
                             runner=runner, root=settings.root, day=day)
            work.save("triage", {"items": [i.to_dict() for i in new_items], "outcome": outcome.to_dict()})
        t_data = work.load("triage") or {"items": [], "outcome": TriageOutcome("triaged", [], 0.0).to_dict()}
        new_items = [Item.from_dict(d) for d in t_data["items"]]
        outcome = TriageOutcome.from_dict(t_data["outcome"])

        # 5. render + finalize -----------------------------------------------------
        md = render_digest(day, {i.id: i for i in new_items}, outcome, warnings, total_collected=len(all_items))
        shown = shown_item_ids(md)
        digest_path = (work.dir / "digest.md") if dry_run else vault.digest_path(day)
        Vault.write_atomic(digest_path, md)

        try_ids = [t.id for t in sorted(outcome.triaged, key=lambda t: t.score, reverse=True)
                   if t.category == "try" and t.id in shown]
        report = NightlyReport(
            day=day.isoformat(), collected=len(all_items), new=len(new_items), shown=len(shown), mode=outcome.mode,
            warnings=warnings, cost_usd=outcome.cost_usd, digest_path=str(digest_path),
            decisions_added=decisions_added, n_try=len(try_ids), try_ids=try_ids,
        )
        if not dry_run:
            with SeenStore(seen_path) as seen:
                seen.mark(new_items, day, shown, {t.id: t.category for t in outcome.triaged})
            vault.append_line(vault.log, log_line(day, collected=report.collected, new=report.new, n_try=report.n_try,
                                                  mode=report.mode, cost_usd=report.cost_usd, warnings=warnings))
            if warnings or outcome.mode == "untriaged":
                notifier("ai-watch", f"{day.isoformat()}: mode={outcome.mode} / 失敗 {len(warnings)} 件")
        return report
    except Exception as e:
        notifier("ai-watch", f"{day.isoformat()}: FAILED {type(e).__name__}: {str(e)[:120]}")
        if not dry_run:
            try:
                vault.append_line(
                    vault.log,
                    f"## [{day.isoformat()}] nightly | FAILED: {type(e).__name__}: {str(e)[:160]}",
                )
            except Exception:
                pass
        raise
