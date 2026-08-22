from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from .models import Decision, Item, TriagedItem

Mode = Literal["triaged", "untriaged"]


@dataclass
class TriageOutcome:
    mode: Mode
    triaged: list[TriagedItem]
    cost_usd: float
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "triaged": [t.to_dict() for t in self.triaged],
                "cost_usd": self.cost_usd, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TriageOutcome":
        return cls(mode=d["mode"], triaged=[TriagedItem.from_dict(t) for t in d["triaged"]],
                   cost_usd=float(d["cost_usd"]), error=d.get("error", ""))


def _items_payload(items: list[Item]) -> list[dict[str, Any]]:
    return [{
        "id": it.id, "source": it.source, "group": it.group, "mentions": it.mentions,
        "title": it.title, "excerpt": it.excerpt[:600], "metrics": it.metrics,
        "published_at": it.published_at.isoformat() if it.published_at else None, "lang": it.lang,
    } for it in items]


def _decisions_text(decisions: list[Decision]) -> str:
    if not decisions:
        return "（まだ判断履歴はありません）"
    return "\n".join(f"- [{d.decision}] {d.title} ({d.url})" for d in decisions)


def build_prompt(template: str, items: list[Item], profile_md: str, decisions: list[Decision], day: date) -> str:
    return (
        template.replace("{{DATE}}", day.isoformat())
        .replace("{{PROFILE}}", profile_md.strip())
        .replace("{{DECISIONS}}", _decisions_text(decisions))
        .replace("{{ITEMS}}", json.dumps(_items_payload(items), ensure_ascii=False))
    )


def _clamp(v: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def parse_triage(data: dict[str, Any], known_ids: list[str]) -> list[TriagedItem]:
    """出力を TriagedItem に。未知 id は捨て、欠けた id は noise。"""
    known = set(known_ids)
    out: dict[str, TriagedItem] = {}
    for row in data.get("items", []):
        iid = row.get("id")
        if iid not in known or iid in out:
            continue
        signals = row.get("signals") or {}
        out[iid] = TriagedItem(
            id=iid, category=row.get("category", "noise"), score=_clamp(row.get("score"), 0, 100),
            signals={k: _clamp(signals.get(k), 0, 3) for k in ("attention", "tryability", "jp_gap", "relevance")},
            reason=str(row.get("reason") or ""), try_plan=str(row.get("try_plan") or ""),
            article_angle=str(row.get("article_angle") or ""),
        )
    for iid in known_ids:
        if iid not in out:
            out[iid] = TriagedItem(id=iid, category="noise", score=0,
                                   signals={"attention": 0, "tryability": 0, "jp_gap": 0, "relevance": 0}, reason="")
    return list(out.values())


def fallback_rank(items: list[Item]) -> list[TriagedItem]:
    """LLM が使えない日の順位付け: log(metrics 合計) と mentions 数。全て read。"""
    def score(it: Item) -> int:
        m = sum(it.metrics.values())
        return int(min(100, 20 * math.log10(m + 1) + 15 * (len(it.mentions) - 1)))
    ranked = sorted(items, key=score, reverse=True)
    return [TriagedItem(id=it.id, category="read", score=score(it),
                        signals={"attention": 0, "tryability": 0, "jp_gap": 0, "relevance": 0},
                        reason="未トリアージ（metrics 順）") for it in ranked]


def triage(
    items: list[Item], *, profile_md: str, decisions: list[Decision], runner: Any, root: Path, day: date,
    budget_usd: float = 2.0,
) -> TriageOutcome:
    if not items:
        return TriageOutcome("triaged", [], 0.0)
    template = (root / "prompts" / "triage.md").read_text(encoding="utf-8")
    schema = json.loads((root / "schemas" / "triage.schema.json").read_text(encoding="utf-8"))
    prompt = build_prompt(template, items, profile_md, decisions, day)
    res = runner.run(prompt, schema, budget_usd=budget_usd, effort="low", retries=1)
    if not res.ok:
        return TriageOutcome("untriaged", fallback_rank(items), res.cost_usd, res.error)
    return TriageOutcome("triaged", parse_triage(res.data, [it.id for it in items]), res.cost_usd)
