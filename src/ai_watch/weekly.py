from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Item, TriagedItem
from .trends import Trend, _qualifying, _terms

# 1 週間分の triage.json から、話題の伸び・落ち、起点になった記事、業界の動きを集計する。
# 日次の baseline（定番語の除外）は 1 日単位の調整なので使わず、週の合計件数を前週と比べる。

WEEK_DAYS = 7
MIN_ITEMS = 8        # 週合計での話題の下限（日次は 5）
MIN_SOURCES = 3
TOP_TERMS = 8
MIN_NOTABLE = 3      # 選別で noise 以外になった記事（または公式）がこの件数に届かない語は話題にしない
OVERLAP = 0.5        # 記事の重なりがこの割合以上の語は同じ話題にまとめる
# 毎週どこかで件数が揺れるだけの一般的な技術語。日次の _STOPWORDS に加えて週次だけ外す
_GENERIC = frozenset("""
python rust typescript javascript java go node linux windows mac macos ios android gpu cpu cli git hub
web docker aws azure vscode cloud browser terminal deep
""".split()) | frozenset({"サイト", "セキュリティ", "インストール", "ターミナル"})
_CATEGORY_RANK = {"update": 4, "industry": 3, "try": 2, "read": 1, "noise": 0}


@dataclass
class Week:
    start: date
    end: date
    days: list[date] = field(default_factory=list)          # triage.json が読めた日
    missing: list[date] = field(default_factory=list)
    items: dict[str, Item] = field(default_factory=dict)     # id で重複を除いたアイテム
    triaged: dict[str, TriagedItem] = field(default_factory=dict)
    first_seen: dict[str, date] = field(default_factory=dict)


def load_week(data_dir: Path, end: date, days: int = WEEK_DAYS) -> Week:
    week = Week(start=end - timedelta(days=days - 1), end=end)
    for n in range(days - 1, -1, -1):
        day = end - timedelta(days=n)
        path = data_dir / "work" / day.isoformat() / "triage.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = [Item.from_dict(d) for d in data["items"] + data.get("held_items", [])]
            triaged = [TriagedItem.from_dict(t) for t in data["outcome"]["triaged"]]
        except (OSError, ValueError, KeyError, TypeError):
            week.missing.append(day)
            continue
        week.days.append(day)
        for it in items:
            week.items.setdefault(it.id, it)
            week.first_seen.setdefault(it.id, day)
        for t in triaged:
            prev = week.triaged.get(t.id)
            if prev is None or (_CATEGORY_RANK[t.category], t.score) > (_CATEGORY_RANK[prev.category], prev.score):
                week.triaged[t.id] = t
    return week


def _term_counts(items: list[Item]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for it in items:
        for key in _terms(it.title):
            counts[key] += 1
    return counts


def _when(it: Item, week: Week) -> datetime:
    if it.published_at:
        return it.published_at
    return datetime.combine(week.first_seen.get(it.id, week.end), datetime.min.time(), tzinfo=timezone.utc)


def _item_ref(it: Item, week: Week) -> dict[str, Any]:
    t = week.triaged.get(it.id)
    return {"id": it.id, "title": it.title, "url": it.url, "source": it.source, "group": it.group,
            "date": _when(it, week).date().isoformat(), "category": t.category if t else "noise",
            "summary": t.summary if t else ""}


def _status(count: int, before: int) -> str:
    if before == 0:
        return "new"
    if count >= 1.5 * before:
        return "rising"
    return "falling" if count <= 0.5 * before else "steady"


def _notable(it: Item, week: Week) -> bool:
    t = week.triaged.get(it.id)
    return it.group == "official" or (t is not None and t.category != "noise")


def _merge(trends: list[Trend]) -> list[tuple[list[Trend], set[str]]]:
    """Muse / Spark / Meta のように同じ記事群を指す語を 1 つの話題にまとめる（件数の多い語を代表にする）。"""
    groups: list[tuple[list[Trend], set[str]]] = []
    for t in sorted(trends, key=lambda t: (-len(t.item_ids), t.term)):
        ids = set(t.item_ids)
        for terms, members in groups:
            head = set(terms[0].item_ids)
            if len(ids & head) >= OVERLAP * min(len(ids), len(head)):
                terms.append(t)
                members |= ids
                break
        else:
            groups.append(([t], ids))
    return groups


def _label(terms: list[Trend]) -> str:
    """DeepSeek と Seek のように、他の語に含まれる部分語は表示から外す。"""
    labels = [t.label for t in terms]
    kept = [l for l in labels if not any(l != o and l.lower() in o.lower() for o in labels)]
    return " / ".join(kept[:3])


def _origin(members: list[Item], week: Week) -> Item:
    """期間内に出た記事のうち最も早いもの。注目記事（noise 以外・公式）を優先する。"""
    since = datetime.combine(week.start - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    in_period = [it for it in members if _when(it, week) >= since] or members
    pool = [it for it in in_period if _notable(it, week)] or in_period
    return min(pool, key=lambda it: _when(it, week))


def _topics(this: Week, prev: Week) -> tuple[list[dict[str, Any]], set[str]]:
    candidates = [
        t for t in _qualifying(list(this.items.values()), MIN_ITEMS, MIN_SOURCES)
        if t.term not in _GENERIC and sum(_notable(this.items[i], this) for i in t.item_ids) >= MIN_NOTABLE
    ]
    prev_terms = [set(_terms(it.title)) for it in prev.items.values()]
    topics = []
    for terms, ids in _merge(candidates):
        keys = {t.term for t in terms}
        members = [this.items[i] for i in ids]
        official = sorted((it for it in members if it.group == "official"), key=lambda it: _when(it, this))
        before = sum(1 for ts in prev_terms if ts & keys)
        top = sorted((it for it in members if _notable(it, this)),
                     key=lambda it: this.triaged[it.id].score if it.id in this.triaged else 0, reverse=True)
        topics.append({
            "label": _label(terms), "terms": sorted(keys),
            "count": len(members), "prev_count": before, "status": _status(len(members), before),
            "sources": sorted({m for it in members for m in it.mentions}),
            "origin": _item_ref(_origin(members, this), this),
            "official": [_item_ref(it, this) for it in official[:3]],
            "top": [_item_ref(it, this) for it in top[:3]],
        })
    topics.sort(key=lambda t: (t["status"] in ("steady", "falling"), -(t["count"] - t["prev_count"]), -t["count"]))
    return topics, {k for t in topics for k in t["terms"]}


def aggregate(this: Week, prev: Week) -> dict[str, Any]:
    items = list(this.items.values())
    topics, topic_terms = _topics(this, prev)
    this_counts = _term_counts(items)
    falling = [
        {"label": t.label, "count": this_counts.get(t.term, 0), "prev_count": len(t.item_ids)}
        for t in _qualifying(list(prev.items.values()), MIN_ITEMS, MIN_SOURCES)
        if this_counts.get(t.term, 0) <= 0.5 * len(t.item_ids) and t.term not in topic_terms | _GENERIC
        and sum(_notable(prev.items[i], prev) for i in t.item_ids) >= MIN_NOTABLE
    ]
    falling += [{"label": t["label"], "count": t["count"], "prev_count": t["prev_count"]}
                for t in topics if t["status"] == "falling"]
    falling.sort(key=lambda t: t["count"] - t["prev_count"])

    def picked(category: str, limit: int) -> list[dict[str, Any]]:
        ts = sorted((t for t in this.triaged.values() if t.category == category and t.id in this.items),
                    key=lambda t: t.score, reverse=True)[:limit]
        return [{**_item_ref(this.items[t.id], this), "score": t.score, "implication": t.implication,
                 "reason": t.reason} for t in ts]

    return {
        "period": {"start": this.start.isoformat(), "end": this.end.isoformat(),
                   "days": [d.isoformat() for d in this.days], "missing": [d.isoformat() for d in this.missing]},
        "previous": {"start": prev.start.isoformat(), "end": prev.end.isoformat(),
                     "days": [d.isoformat() for d in prev.days], "missing": [d.isoformat() for d in prev.missing]},
        "items_total": len(items),
        "topics": [t for t in topics if t["status"] in ("new", "rising")][:TOP_TERMS],
        "continuing": [{"label": t["label"], "count": t["count"], "prev_count": t["prev_count"]}
                       for t in topics if t["status"] == "steady"][:TOP_TERMS],
        "falling": falling[:5],
        "updates": picked("update", 12),
        "industry": picked("industry", 10),
        "try": picked("try", 6),
        "read": picked("read", 6),
    }


def build_prompt(template: str, agg: dict[str, Any], profile_md: str) -> str:
    return (template.replace("{{PROFILE}}", profile_md.strip())
            .replace("{{AGGREGATE}}", json.dumps(agg, ensure_ascii=False, indent=1)))


def _link(ref: dict[str, Any]) -> str:
    return f"[{ref['title']}]({ref['url']})（{ref['source']}・{ref['date']}）"


def render_markdown(agg: dict[str, Any], narrative: dict[str, Any] | None) -> str:
    p = agg["period"]
    lines = [f"# 週次トレンド {p['start']}〜{p['end']}", ""]
    lines.append(f"対象 {len(p['days'])} 日・{agg['items_total']} 件"
                 f"（前週 {agg['previous']['start']}〜{agg['previous']['end']} と比較）")
    if p["missing"]:
        lines.append(f"> データなし: {', '.join(p['missing'])}")
    if agg["previous"]["missing"]:
        lines.append(f"> 前週のデータなし: {', '.join(agg['previous']['missing'])}")
    lines.append("")

    if narrative:
        lines += ["## 今週の流れ", ""] + [f"- {s}" for s in narrative.get("flow", [])] + [""]
        lines += ["## 次の一手", ""]
        for m in narrative.get("next_moves", []):
            lines.append(f"- **[{m.get('kind', '')}] {m.get('title', '')}** — {m.get('why', '')}")
        lines.append("")

    lines += ["## 話題の伸び", "", "| 話題 | 今週 | 前週 | 状態 | 起点 | 公式の一次情報 |", "|---|---|---|---|---|---|"]
    status = {"new": "新規", "rising": "伸び"}
    for t in agg["topics"]:
        official = "<br>".join(_link(r) for r in t["official"]) or "—"
        lines.append(f"| {t['label']} | {t['count']} | {t['prev_count']} | {status[t['status']]} "
                     f"| {_link(t['origin'])} | {official} |")
    if not agg["topics"]:
        lines.append("| （前週より伸びた話題はありません） | | | | | |")
    if agg["continuing"]:
        lines += ["", "続いている話題: " + "、".join(f"{t['label']}（{t['prev_count']}→{t['count']}）" for t in agg["continuing"])]
    if agg["falling"]:
        lines += ["", "落ちた話題: " + "、".join(f"{t['label']}（{t['prev_count']}→{t['count']}）" for t in agg["falling"])]
    lines.append("")

    if agg["industry"]:
        lines += ["## 業界の動き", ""]
        for r in agg["industry"]:
            lines.append(f"- {_link(r)}")
            if r["implication"]:
                lines.append(f"  - 示唆: {' '.join(r['implication'].split())}")
        lines.append("")
    if agg["updates"]:
        lines += ["## 公式アップデート", ""] + [f"- {_link(r)}" for r in agg["updates"]] + [""]
    if agg["try"]:
        lines += ["## 試す候補（週の上位）", ""] + [f"- {_link(r)}" for r in agg["try"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


WEEKLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["flow", "next_moves"],
    "properties": {
        "flow": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "next_moves": {"type": "array", "maxItems": 3, "items": {
            "type": "object", "required": ["kind", "title", "why"],
            "properties": {"kind": {"enum": ["試す", "発信", "様子見"]}, "title": {"type": "string"},
                           "why": {"type": "string"}}}},
    },
}


def weekly(data_dir: Path, end: date, *, root: Path, profile_md: str, runner: Any | None,
           budget_usd: float = 1.0) -> tuple[dict[str, Any], str, float]:
    this = load_week(data_dir, end)
    prev = load_week(data_dir, end - timedelta(days=WEEK_DAYS))
    agg = aggregate(this, prev)
    narrative, cost = None, 0.0
    if runner is not None:
        template = (root / "prompts" / "weekly.md").read_text(encoding="utf-8")
        res = runner.run(build_prompt(template, agg, profile_md), WEEKLY_SCHEMA, budget_usd=budget_usd,
                         effort="medium", retries=1)
        cost = res.cost_usd
        if res.ok:
            narrative = res.data
    agg["narrative"] = narrative
    return agg, render_markdown(agg, narrative), cost
