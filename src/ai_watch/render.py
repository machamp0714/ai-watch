from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .models import Item, TriagedItem
from .triage import TriageOutcome

_BLOCK_ID = re.compile(r"\^(aw-[0-9a-f]{8})\b")


@dataclass(frozen=True)
class Limits:
    try_: int = 3
    read: int = 3
    update: int = 5


def _clean(s: str) -> str:
    return " ".join(re.sub(r"[*\[\]^]", "", s or "").split())


def _src(it: Item) -> str:
    extra = len(it.mentions) - 1
    return f"{it.source} +{extra}" if extra > 0 else it.source


def _link(it: Item) -> str:
    return f"([{_src(it)}]({it.url}))"


def _one_line(s: str) -> str:
    return " / ".join(line.strip() for line in (s or "").splitlines() if line.strip())


def _try_lines(it: Item, t: TriagedItem) -> list[str]:
    lines = [f"- [ ] 🧪 **{_clean(it.title)}** — {_clean(t.reason)} {_link(it)} ^{it.id}"]
    if t.try_plan:
        lines.append(f"  - 試し方: {_clean(_one_line(t.try_plan))}")
    if t.article_angle:
        lines.append(f"  - 記事の切り口: {_clean(_one_line(t.article_angle))}")
    return lines


def _update_line(it: Item, t: TriagedItem) -> str:
    return f"- [ ] 📣 **{_clean(it.title)}** — {_clean(t.reason)} {_link(it)} ^{it.id}"


def _read_line(it: Item, t: TriagedItem) -> str:
    return f"- 📖 **{_clean(it.title)}** — {_clean(t.reason)} {_link(it)} ^{it.id}"


def _overflow_line(it: Item, t: TriagedItem) -> str:
    return f"- {t.category} {t.score} **{_clean(it.title)}** {_link(it)}"


def shown_item_ids(md: str) -> set[str]:
    return set(_BLOCK_ID.findall(md))


def _frontmatter(day: date, mode: str, total: int, shown: int, cost: float, warnings: list[str]) -> str:
    lines = ["---", "type: record", f"date: {day.isoformat()}", f"mode: {mode}",
             f"items_total: {total}", f"items_shown: {shown}", f"cost_usd: {cost:.2f}"]
    if warnings:
        lines.append("warnings:")
        lines += [f'- "{w.replace(chr(34), chr(39))}"' for w in warnings]
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
        body += [f"> ⚠ 取得失敗: {w}" for w in warnings] + [""]

    if outcome.mode == "untriaged":
        body += [f"> ⚠ トリアージ失敗（{outcome.error}）。metrics 順の生リストです。", "",
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
        overflow = by_cat["try"][limits.try_:] + by_cat["update"][limits.update:] + by_cat["read"][limits.read:]

        if not any(by_cat.values()):
            body += ["新着はありませんでした。", ""]
        body += ["## 🧪 試す候補（[x] で backlog へ）"]
        for t in by_cat["try"][:limits.try_]:
            body += _try_lines(items[t.id], t)
        body += ["", "## 📣 公式アップデート（[x] で X 投稿待ちへ）"]
        body += [_update_line(items[t.id], t) for t in by_cat["update"][:limits.update]]
        body += ["", "## 📖 読む"]
        body += [_read_line(items[t.id], t) for t in by_cat["read"][:limits.read]]

    if overflow:
        body += ["", f"<details><summary>その他の候補 ({len(overflow)} 件)</summary>", ""]
        body += [_overflow_line(items[t.id], t) for t in overflow]
        body += ["", "</details>"]

    text = "\n".join(body).rstrip() + "\n"
    shown = len(shown_item_ids(text))
    return _frontmatter(day, outcome.mode, total_collected, shown, outcome.cost_usd, warnings) + "\n" + text
