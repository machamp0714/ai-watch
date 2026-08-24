from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .models import Item, TriagedItem
from .triage import TriageOutcome

_BLOCK_ID = re.compile(r"\^(aw-[0-9a-f]{8})\b")

TABLE_HEADER = ["| 記事 | ソース | 要約 |", "|---|---|---|"]


@dataclass(frozen=True)
class Limits:
    try_: int = 3
    read: int = 3
    update: int = 5


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


def render_digest(
    day: date, items: dict[str, Item], outcome: TriageOutcome, warnings: list[str], *,
    total_collected: int, limits: Limits = Limits(),
) -> str:
    ranked = sorted((t for t in outcome.triaged if t.id in items), key=lambda t: t.score, reverse=True)
    body: list[str] = [f"# AI Watch {day.isoformat()}", ""]
    if warnings:
        body += [f"> ⚠ 取得失敗: {_clean_warning(w)}" for w in warnings] + [""]

    if outcome.mode == "untriaged":
        body += [f"> ⚠ トリアージ失敗（{_clean_warning(outcome.error)}）。metrics 順の生リストです。", "",
                 "## ⚠ 未トリアージ（metrics 順）"]
        head, tail = ranked[:15], ranked[15:]
        for t in head:
            body += _try_lines(items[t.id], t)
        overflow = tail
    else:
        by_cat: dict[str, list[TriagedItem]] = {"try": [], "update": [], "read": []}
        for t in ranked:
            if t.category in by_cat:
                by_cat[t.category].append(t)
        overflow = sorted(by_cat["try"][limits.try_:] + by_cat["update"][limits.update:] + by_cat["read"][limits.read:],
                          key=lambda t: t.score, reverse=True)

        if not any(by_cat.values()):
            body += ["新着はありませんでした。", ""]
        body += ["## 🧪 試す候補（[x] で backlog へ）"]
        for t in by_cat["try"][:limits.try_]:
            body += _try_lines(items[t.id], t)
        body += ["", "## 📣 公式アップデート（[x] で X 投稿待ちへ）"]
        updates = by_cat["update"][:limits.update]
        if updates:
            for t in updates:
                body += _update_lines(items[t.id], t)
        else:
            body += ["（なし。新しい公式リリースはありませんでした）"]
        body += ["", "## 📖 読む", ""]
        body += _table([(items[t.id], t) for t in by_cat["read"][:limits.read]])

    if overflow:
        body += ["", f"## 👀 注目（{len(overflow)} 件）", ""]
        body += _table([(items[t.id], t) for t in overflow])

    text = "\n".join(body).rstrip() + "\n"
    shown = len(shown_item_ids(text))
    return _frontmatter(day, outcome.mode, total_collected, shown, outcome.cost_usd, warnings) + "\n" + text
