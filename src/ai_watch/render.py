from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .models import Item, TriagedItem
from .trends import Trend
from .triage import TriageOutcome

_BLOCK_ID = re.compile(r"\^(aw-[0-9a-f]{8})\b")

TABLE_HEADER = ["| 記事 | ソース | 要約 |", "|---|---|---|"]


@dataclass(frozen=True)
class Limits:
    try_: int = 3
    read: int = 3
    update: int = 5
    trend_items: int = 3


@dataclass(frozen=True)
class DigestSelection:
    try_: list[tuple[Item, TriagedItem]]
    update: list[tuple[Item, TriagedItem]]
    read: list[tuple[Item, TriagedItem]]
    overflow: list[tuple[Item, TriagedItem]]


def _clean(s: str) -> str:
    return " ".join(re.sub(r"[*\[\]^]", "", s or "").split())


def _clean_warning(w: str) -> str:
    return " ".join((w or "").replace("^", "").split())


def _src(it: Item) -> str:
    extra = len(it.mentions) - 1
    return f"{it.source} +{extra}" if extra > 0 else it.source


def _url(it: Item) -> str:
    return it.url.replace("(", "%28").replace(")", "%29")


def _link(it: Item) -> str:
    return f"([{_src(it)}]({_url(it)}))"


def _one_line(s: str) -> str:
    return " / ".join(line.strip() for line in (s or "").splitlines() if line.strip())


def _summary_lines(t: TriagedItem) -> list[str]:
    """summary を行ごとに（先頭の箇条書き記号は落とす）。無ければ reason で代用。"""
    lines = [_clean(re.sub(r"^\s*[-*・]\s*", "", line)) for line in (t.summary or "").splitlines()]
    lines = [line for line in lines if line]
    return lines or ([_clean(t.reason)] if _clean(t.reason) else [])


def _cell(s: str) -> str:
    return _clean(s).replace("|", "\\|")


def _try_lines(it: Item, t: TriagedItem) -> list[str]:
    title = _clean(it.title) or "(no title)"
    lines = [f"- [ ] 🧪 **{title}** — {_clean(t.reason)} {_link(it)} ^{it.id}"]
    if t.try_plan:
        lines.append(f"  - 試し方: {_clean(_one_line(t.try_plan))}")
    if t.article_angle:
        lines.append(f"  - 記事の切り口: {_clean(_one_line(t.article_angle))}")
    return lines


def _update_lines(it: Item, t: TriagedItem) -> list[str]:
    title = _clean(it.title) or "(no title)"
    return [f"- [ ] 📣 **{title}** {_link(it)} ^{it.id}"] + [f"  - {line}" for line in _summary_lines(t)]


def _table_row(it: Item, t: TriagedItem) -> str:
    """記事 / ソース / 要約 の 1 行。ブロック ID は Obsidian コメント（%% %%）で隠して回収用に残す。"""
    title = _cell(it.title) or "(no title)"
    summary = _cell(" ".join(_summary_lines(t)))
    return f"| **{title}** | [{_src(it)}]({_url(it)}) | {summary} %%^{it.id}%% |"


def _table(rows: list[tuple[Item, TriagedItem]]) -> list[str]:
    if not rows:
        return ["（なし）"]
    return TABLE_HEADER + [_table_row(it, t) for it, t in rows]


def _trend_members(
    trend: Trend, items: dict[str, Item], outcome: TriageOutcome, limit: int
) -> list[tuple[Item, TriagedItem | None]]:
    """話題クラスタの代表記事: noise 以外を score 順、足りなければ反響順で補う。"""
    by_id = {t.id: t for t in outcome.triaged}
    members = [i for i in trend.item_ids if i in items]

    def key(i: str) -> tuple[int, int, int]:
        t = by_id.get(i)
        it = items[i]
        judged = t is not None and t.category != "noise"
        return (int(judged), t.score if t else 0, sum(max(0, v) for v in it.metrics.values()) + len(it.mentions))

    members.sort(key=key, reverse=True)
    return [(items[i], by_id.get(i)) for i in members[:limit]]


def _trend_lines(trend: Trend, items: dict[str, Item], outcome: TriageOutcome, limit: int) -> list[str]:
    # ブロック ID は付けない（チェック回収・shown の対象は各セクションの記事だけ）
    lines = [f"- **{_clean(trend.label)}**（{len(trend.item_ids)} 件・{len(trend.sources)} ソース）"]
    for it, t in _trend_members(trend, items, outcome, limit):
        summary = " ".join(_summary_lines(t)) if t else ""
        tail = f" — {summary}" if summary else ""
        lines.append(f"  - [{_clean(it.title) or '(no title)'}]({_url(it)})（{_src(it)}）{tail}")
    return lines


def shown_item_ids(md: str) -> set[str]:
    return set(_BLOCK_ID.findall(md))


def _frontmatter(day: date, mode: str, total: int, shown: int, cost: float, warnings: list[str]) -> str:
    lines = ["---", "type: record", f"date: {day.isoformat()}", f"mode: {mode}",
             f"items_total: {total}", f"items_shown: {shown}", f"cost_usd: {cost:.2f}"]
    if warnings:
        lines.append("warnings:")
        lines += [f"- '{_clean_warning(w).replace(chr(39), chr(39)*2)}'" for w in warnings]
    else:
        lines.append("warnings: []")
    lines.append("---")
    return "\n".join(lines)


def _select_digest(
    items: dict[str, Item], outcome: TriageOutcome, limits: Limits
) -> DigestSelection:
    ranked = sorted(
        (t for t in outcome.triaged if t.id in items),
        key=lambda t: t.score,
        reverse=True,
    )
    if outcome.mode == "untriaged":
        return DigestSelection(
            try_=[(items[t.id], t) for t in ranked[:15]],
            update=[],
            read=[],
            overflow=[(items[t.id], t) for t in ranked[15:]],
        )

    by_cat: dict[str, list[TriagedItem]] = {"try": [], "update": [], "read": []}
    for triaged in ranked:
        if triaged.category in by_cat:
            by_cat[triaged.category].append(triaged)
    overflow = sorted(
        by_cat["try"][limits.try_:]
        + by_cat["update"][limits.update:]
        + by_cat["read"][limits.read:],
        key=lambda triaged: triaged.score,
        reverse=True,
    )
    return DigestSelection(
        try_=[(items[t.id], t) for t in by_cat["try"][:limits.try_]],
        update=[(items[t.id], t) for t in by_cat["update"][:limits.update]],
        read=[(items[t.id], t) for t in by_cat["read"][:limits.read]],
        overflow=[(items[t.id], t) for t in overflow],
    )


def _digest_item(item: Item, triaged: TriagedItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title.strip() or "(no title)",
        "url": item.url,
        "source": item.source,
        "mentions": list(item.mentions),
        "lang": item.lang,
        "score": triaged.score,
        "reason": _clean(triaged.reason),
        "summary": _summary_lines(triaged),
        "try_plan": _clean(_one_line(triaged.try_plan)),
        "article_angle": _clean(_one_line(triaged.article_angle)),
        "published_at": item.published_at.isoformat() if item.published_at else None,
    }


def render_digest_data(
    day: date,
    items: dict[str, Item],
    outcome: TriageOutcome,
    warnings: list[str],
    *,
    total_collected: int,
    limits: Limits = Limits(),
) -> dict[str, Any]:
    selection = _select_digest(items, outcome, limits)
    sections = {
        "try": [_digest_item(item, triaged) for item, triaged in selection.try_],
        "update": [
            _digest_item(item, triaged) for item, triaged in selection.update
        ],
        "read": [_digest_item(item, triaged) for item, triaged in selection.read],
        "overflow": [
            _digest_item(item, triaged) for item, triaged in selection.overflow
        ],
    }
    shown = {item["id"] for section in sections.values() for item in section}
    trends = [
        {
            "label": _clean(trend.label),
            "count": len(trend.item_ids),
            "sources": list(trend.sources),
            "items": [
                {"title": it.title.strip() or "(no title)", "url": it.url, "source": it.source,
                 "summary": _summary_lines(t) if t else []}
                for it, t in _trend_members(trend, items, outcome, limits.trend_items)
            ],
        }
        for trend in outcome.trends
    ]
    return {
        "date": day.isoformat(),
        "mode": outcome.mode,
        "items_total": total_collected,
        "items_shown": len(shown),
        "cost_usd": outcome.cost_usd,
        "warnings": [_clean_warning(warning) for warning in warnings],
        "trends": trends,
        "sections": sections,
    }


def render_digest(
    day: date, items: dict[str, Item], outcome: TriageOutcome, warnings: list[str], *,
    total_collected: int, limits: Limits = Limits(),
) -> str:
    selection = _select_digest(items, outcome, limits)
    body: list[str] = [f"# AI Watch {day.isoformat()}", ""]
    if warnings:
        body += [f"> ⚠ 取得失敗: {_clean_warning(w)}" for w in warnings] + [""]
    if outcome.trends:
        body += ["## 🔥 今日の話題"]
        for trend in outcome.trends:
            body += _trend_lines(trend, items, outcome, limits.trend_items)
        body += [""]

    if outcome.mode == "untriaged":
        body += [f"> ⚠ トリアージ失敗（{_clean_warning(outcome.error)}）。metrics 順の生リストです。", "",
                 "## ⚠ 未トリアージ（metrics 順）"]
        for item, triaged in selection.try_:
            body += _try_lines(item, triaged)
    else:
        if not any((selection.try_, selection.update, selection.read)):
            body += ["新着はありませんでした。", ""]
        body += ["## 🧪 試す候補（[x] で backlog へ）"]
        for item, triaged in selection.try_:
            body += _try_lines(item, triaged)
        body += ["", "## 📣 公式アップデート（[x] で X 投稿待ちへ）"]
        if selection.update:
            for item, triaged in selection.update:
                body += _update_lines(item, triaged)
        else:
            body += ["（なし。新しい公式リリースはありませんでした）"]
        body += ["", "## 📖 読む", ""]
        body += _table(selection.read)

    if selection.overflow:
        body += ["", f"## 👀 注目（{len(selection.overflow)} 件）", ""]
        body += _table(selection.overflow)

    text = "\n".join(body).rstrip() + "\n"
    shown = len(shown_item_ids(text))
    return _frontmatter(day, outcome.mode, total_collected, shown, outcome.cost_usd, warnings) + "\n" + text
