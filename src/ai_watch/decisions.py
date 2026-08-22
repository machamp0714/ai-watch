from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .models import Decision
from .vault import Vault

# render.py が出す行の契約: "- [ ] 🧪 **title** — reason ([src](url)) ^aw-xxxxxxxx"
LINE_RE = re.compile(
    r"^\s*- \[(?P<mark>[ xX])\] (?P<emoji>🧪|📣) \*\*(?P<title>.+?)\*\*(?P<rest>.*?)\^(?P<id>aw-[0-9a-f]{8})\s*$",
    re.M,
)
URL_RE = re.compile(r"\((https?://[^\s)]+)\)")


def parse_digest(md: str, digest_date: date, today: date, implicit_skip_days: int = 3) -> list[Decision]:
    out: list[Decision] = []
    stale = (today - digest_date).days >= implicit_skip_days
    for m in LINE_RE.finditer(md):
        url_m = URL_RE.search(m["rest"])
        url = url_m.group(1) if url_m else ""
        common = dict(id=m["id"], date=today, digest_date=digest_date, title=m["title"].strip(), url=url)
        if m["mark"] in "xX":
            out.append(Decision(decision="try" if m["emoji"] == "🧪" else "share", **common))
        elif m["emoji"] == "🧪" and stale:
            out.append(Decision(decision="skip_implicit", **common))
    return out


class DecisionStore:
    """人の判断の追記専用ログ (jsonl)。(id, decision) で一意。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[Decision]:
        if not self.path.exists():
            return []
        return [Decision.from_dict(json.loads(l)) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def filter_new(self, decisions: list[Decision]) -> list[Decision]:
        """決定の中で新規（まだ記録されていない）ものをフィルタする。書き込みは行わない。"""
        existing = self.load()
        keys = {(d.id, d.decision) for d in existing}
        positive = {d.id for d in existing if d.decision in ("try", "share")}
        positive |= {d.id for d in decisions if d.decision in ("try", "share")}
        added: list[Decision] = []
        for d in decisions:
            if (d.id, d.decision) in keys:
                continue
            if d.decision == "skip_implicit" and d.id in positive:
                continue
            keys.add((d.id, d.decision))
            added.append(d)
        return added

    def append(self, decisions: list[Decision]) -> None:
        """決定を記録に追加する（新規チェックなし）。"""
        if decisions:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                for d in decisions:
                    f.write(json.dumps(d.to_dict(), ensure_ascii=False) + "\n")

    def append_unique(self, decisions: list[Decision]) -> list[Decision]:
        added = self.filter_new(decisions)
        self.append(added)
        return added

    def recent(self, n: int = 30) -> list[Decision]:
        return sorted(self.load(), key=lambda d: (d.date, d.id), reverse=True)[:n]


def backlog_line(d: Decision) -> str:
    return f"- [ ] 🧪 **{d.title}** (追加 {d.date.isoformat()}) ([link]({d.url})) ^{d.id}"


def outputs_line(d: Decision) -> str:
    return f"- [ ] 📣 **{d.title}** ({d.date.isoformat()}) ([link]({d.url})) ^{d.id}"


def apply_decisions(new: list[Decision], vault: Vault) -> None:
    for d in new:
        if d.decision == "try":
            vault.insert_under_heading(vault.backlog, "## 候補", backlog_line(d), dedupe_key=f"^{d.id}")
        elif d.decision == "share":
            vault.insert_under_heading(vault.outputs, "## 投稿待ち", outputs_line(d), dedupe_key=f"^{d.id}")


def sync_decisions(vault: Vault, store: DecisionStore, today: date, *, apply: bool = True, days: int = 7) -> list[Decision]:
    """直近 days 日のダイジェストを読み直してチェックを回収し、store と vault に反映する。毎晩呼んでも冪等。"""
    found: list[Decision] = []
    for digest_date, md in vault.recent_digests(today, days=days):
        found.extend(parse_digest(md, digest_date, today))
    added = store.filter_new(found)
    if apply:
        apply_decisions(added, vault)
    store.append(added)
    return added
