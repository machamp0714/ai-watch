from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from .models import Decision, Item, TriagedItem
from .trends import Trend, detect_trends

Mode = Literal["triaged", "untriaged"]

# 読者が常に見たい公式リリース。LLM が noise に落としても update に昇格する
ALWAYS_UPDATE_SOURCES = {"claude-code-changelog", "codex-releases"}

# プロンプトに載せる excerpt の上限。公式（changelog / release notes）は要約の材料なので長めに渡す
_EXCERPT_CHARS = {"official": 2000}

# 反響の大きい記事は LLM が noise に落としても read に戻す（metrics のキー → 閾値）
POPULAR_THRESHOLDS = {"likes": 50, "points": 200}
# 話題クラスタごとに noise から救う代表記事の数
TREND_REPRESENTATIVES = 2


@dataclass
class TriageOutcome:
    mode: Mode
    triaged: list[TriagedItem]
    cost_usd: float
    error: str = ""
    trends: list[Trend] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "triaged": [t.to_dict() for t in self.triaged],
                "cost_usd": self.cost_usd, "error": self.error, "trends": [t.to_dict() for t in self.trends]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TriageOutcome":
        return cls(mode=d["mode"], triaged=[TriagedItem.from_dict(t) for t in d["triaged"]],
                   cost_usd=float(d["cost_usd"]), error=d.get("error", ""),
                   trends=[Trend.from_dict(t) for t in d.get("trends", [])])


def _items_payload(items: list[Item]) -> list[dict[str, Any]]:
    return [{
        "id": it.id, "source": it.source, "group": it.group, "mentions": it.mentions,
        "title": it.title, "excerpt": it.excerpt[:_EXCERPT_CHARS.get(it.group, 600)], "metrics": it.metrics,
        "published_at": it.published_at.isoformat() if it.published_at else None, "lang": it.lang,
    } for it in items]


def _decisions_text(decisions: list[Decision]) -> str:
    if not decisions:
        return "（まだ判断履歴はありません）"
    return "\n".join(f"- [{d.decision}] {d.title} ({d.url})" for d in decisions)


def _trends_text(trends: list[Trend]) -> str:
    if not trends:
        return "（今日は目立った話題クラスタはありません）"
    return "\n".join(
        f"- {t.label}: {len(t.item_ids)} 件・{len(t.sources)} ソース（{', '.join(t.sources)}）"
        f" ids={', '.join(t.item_ids)}"
        for t in trends
    )


def build_prompt(
    template: str, items: list[Item], profile_md: str, decisions: list[Decision], day: date,
    trends: list[Trend] | None = None,
) -> str:
    return (
        template.replace("{{DATE}}", day.isoformat())
        .replace("{{PROFILE}}", profile_md.strip())
        .replace("{{DECISIONS}}", _decisions_text(decisions))
        .replace("{{TRENDS}}", _trends_text(trends or []))
        .replace("{{ITEMS}}", json.dumps(_items_payload(items), ensure_ascii=False))
    )


def _clamp(v: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def parse_triage(data: dict[str, Any], known_ids: list[str], groups: dict[str, str] | None = None) -> list[TriagedItem]:
    """出力を TriagedItem に。未知 id は捨て、欠けた id は noise。
    groups（id → group）が渡された場合、category が update で group が "official" でない
    アイテムは read に格下げする（公式以外を「公式アップデート」として出さないため。
    id が groups に無い場合も安全側に倒して格下げする）。"""
    known = set(known_ids)
    out: dict[str, TriagedItem] = {}
    rows = data.get("items")
    rows = rows if isinstance(rows, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        iid = row.get("id")
        if iid not in known or iid in out:
            continue
        signals = row.get("signals") or {}
        cat = row.get("category")
        category = cat if cat in ("update", "try", "read", "noise") else "noise"
        if groups is not None and category == "update" and groups.get(iid) != "official":
            category = "read"
        out[iid] = TriagedItem(
            id=iid, category=category, score=_clamp(row.get("score"), 0, 100),
            signals={k: _clamp(signals.get(k), 0, 3) for k in ("attention", "tryability", "jp_gap", "relevance")},
            reason=str(row.get("reason") or ""), try_plan=str(row.get("try_plan") or ""),
            article_angle=str(row.get("article_angle") or ""),
            summary=str(row.get("summary") or ""),
        )
    for iid in known_ids:
        if iid not in out:
            out[iid] = TriagedItem(id=iid, category="noise", score=0,
                                   signals={"attention": 0, "tryability": 0, "jp_gap": 0, "relevance": 0}, reason="")
    return list(out.values())


def promote_official(triaged: list[TriagedItem], items: list[Item]) -> list[TriagedItem]:
    """ALWAYS_UPDATE_SOURCES のアイテムが noise なら update（score 10）に昇格し、summary を excerpt の 1 行目で埋める。"""
    by_id = {it.id: it for it in items}
    out = []
    for t in triaged:
        it = by_id.get(t.id)
        if t.category == "noise" and it is not None and it.source in ALWAYS_UPDATE_SOURCES:
            first = next((l.strip(" -*") for l in it.excerpt.splitlines() if l.strip()), "")
            t = TriagedItem(id=t.id, category="update", score=10, signals=t.signals,
                            reason="公式リリース（自動昇格）", summary=first[:120] or "（本文なし）")
        out.append(t)
    return out


def _popularity(it: Item) -> int:
    return sum(max(0, v) for v in it.metrics.values()) + 10 * (len(it.mentions) - 1)


def _excerpt_summary(it: Item) -> str:
    first = next((l.strip(" -*") for l in it.excerpt.splitlines() if l.strip()), "")
    return first[:100]


def promote_popular(triaged: list[TriagedItem], items: list[Item]) -> list[TriagedItem]:
    """POPULAR_THRESHOLDS を超える反響の記事が noise なら read（score 45）に戻す。"""
    by_id = {it.id: it for it in items}
    out = []
    for t in triaged:
        it = by_id.get(t.id)
        popular = it is not None and any(it.metrics.get(k, 0) >= v for k, v in POPULAR_THRESHOLDS.items())
        if t.category == "noise" and popular:
            t = TriagedItem(id=t.id, category="read", score=45, signals={**t.signals, "attention": 3},
                            reason="反響の大きい記事（自動昇格）", summary=t.summary or _excerpt_summary(it))
        out.append(t)
    return out


def promote_trends(triaged: list[TriagedItem], items: list[Item], trends: list[Trend]) -> list[TriagedItem]:
    """話題クラスタの上位 TREND_REPRESENTATIVES 件（LLM の score → 反響順）が noise なら read（score 50）に戻す。"""
    by_id = {it.id: it for it in items}
    by_tid = {t.id: t for t in triaged}
    promote: set[str] = set()
    for trend in trends:
        members = [i for i in trend.item_ids if i in by_id and i in by_tid]
        members.sort(key=lambda i: (by_tid[i].score, _popularity(by_id[i])), reverse=True)
        promote.update(i for i in members[:TREND_REPRESENTATIVES] if by_tid[i].category == "noise")
    out = []
    for t in triaged:
        if t.id in promote:
            it = by_id[t.id]
            t = TriagedItem(id=t.id, category="read", score=50, signals={**t.signals, "attention": 3},
                            reason="今日の話題クラスタの代表（自動昇格）", summary=t.summary or _excerpt_summary(it))
        out.append(t)
    return out


def fallback_rank(items: list[Item]) -> list[TriagedItem]:
    """LLM が使えない日の順位付け: log(metrics 合計) と mentions 数。全て read。"""
    def score(it: Item) -> int:
        m = max(0, sum(it.metrics.values()))
        return max(0, min(100, int(20 * math.log10(m + 1) + 15 * (len(it.mentions) - 1))))
    ranked = sorted(items, key=score, reverse=True)
    return [TriagedItem(id=it.id, category="read", score=score(it),
                        signals={"attention": 0, "tryability": 0, "jp_gap": 0, "relevance": 0},
                        reason="未トリアージ（metrics 順）") for it in ranked]


def triage(
    items: list[Item], *, profile_md: str, decisions: list[Decision], runner: Any, root: Path, day: date,
    budget_usd: float = 2.0, trend_baseline: dict[str, float] | None = None,
) -> TriageOutcome:
    if not items:
        return TriageOutcome("triaged", [], 0.0)
    template = (root / "prompts" / "triage.md").read_text(encoding="utf-8")
    schema = json.loads((root / "schemas" / "triage.schema.json").read_text(encoding="utf-8"))
    trends = detect_trends(items, baseline=trend_baseline)
    prompt = build_prompt(template, items, profile_md, decisions, day, trends)
    res = runner.run(prompt, schema, budget_usd=budget_usd, effort="low", retries=1)
    if not res.ok:
        return TriageOutcome("untriaged", fallback_rank(items), res.cost_usd, res.error, trends)
    groups = {it.id: it.group for it in items}
    triaged = promote_official(parse_triage(res.data, [it.id for it in items], groups=groups), items)
    triaged = promote_trends(promote_popular(triaged, items), items, trends)
    return TriageOutcome("triaged", triaged, res.cost_usd, trends=trends)
