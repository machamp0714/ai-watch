from datetime import date

from ai_watch.decisions import DecisionStore, apply_decisions, parse_digest, sync_decisions
from ai_watch.models import Decision
from ai_watch.vault import Vault

DIGEST = """---
type: record
---
# AI Watch 2026-08-20

## 🧪 試す候補
- [x] 🧪 **Claude Code `/claude-api upgrade` を試す** — 理由 ([changelog](https://github.com/a/b#2.1.239)) ^aw-7f3a9c00
  - 試し方: A / B
- [ ] 🧪 **放置された候補 (v2)** — 理由 ([hn +1](https://e.com/2)) ^aw-1b2c3d00
- [X] 🧪 **大文字 X でも拾う** — r ([x](https://e.com/3)) ^aw-aaaaaaaa

## 📣 公式アップデート
- [x] 📣 **Codex v0.50** — 理由 ([codex-releases](https://github.com/openai/codex/releases/tag/v0.50)) ^aw-0000c0de
- [ ] 📣 **未チェックの update** — r ([openai-news](https://openai.com/x)) ^aw-0000beef

## 📖 読む
- 📖 **読み物** — r ([hn](https://e.com/r)) ^aw-0000read

<details><summary>その他</summary>
- try 70 **溢れ** ([hn](https://e.com/o))
</details>
"""


def test_parse_checked_and_implicit_skip():
    decs = parse_digest(DIGEST, date(2026, 8, 20), date(2026, 8, 23))
    got = {(d.id, d.decision) for d in decs}
    assert got == {("aw-7f3a9c00", "try"), ("aw-aaaaaaaa", "try"), ("aw-0000c0de", "share"), ("aw-1b2c3d00", "skip_implicit")}
    by_id = {d.id: d for d in decs}
    assert by_id["aw-7f3a9c00"].title == "Claude Code `/claude-api upgrade` を試す"
    assert by_id["aw-7f3a9c00"].url == "https://github.com/a/b#2.1.239"
    assert by_id["aw-1b2c3d00"].title == "放置された候補 (v2)" and by_id["aw-1b2c3d00"].url == "https://e.com/2"
    assert by_id["aw-7f3a9c00"].date == date(2026, 8, 23) and by_id["aw-7f3a9c00"].digest_date == date(2026, 8, 20)


def test_no_implicit_skip_before_three_days():
    decs = parse_digest(DIGEST, date(2026, 8, 20), date(2026, 8, 22))
    assert ("aw-1b2c3d00", "skip_implicit") not in {(d.id, d.decision) for d in decs}


def test_store_append_unique_and_recent(tmp_path):
    store = DecisionStore(tmp_path / "decisions.jsonl")
    a = Decision("aw-1", "try", date(2026, 8, 21), date(2026, 8, 20), "A", "https://a")
    b = Decision("aw-2", "skip_implicit", date(2026, 8, 22), date(2026, 8, 19), "B", "https://b")
    assert store.append_unique([a, b, a]) == [a, b]
    assert store.append_unique([a]) == []
    # try/share が既にある id の skip_implicit は記録しない
    assert store.append_unique([Decision("aw-1", "skip_implicit", date(2026, 8, 24), date(2026, 8, 20), "A", "https://a")]) == []
    assert [d.id for d in store.recent(10)] == ["aw-2", "aw-1"]           # 新しい順
    assert DecisionStore(tmp_path / "decisions.jsonl").load() == [a, b]


def test_apply_decisions_writes_backlog_and_outputs(tmp_path):
    v = Vault(tmp_path)
    v.backlog.write_text("# Backlog\n\n## 候補\n\n## 完了\n")
    v.outputs.write_text("# Outputs\n\n## 投稿待ち\n\n## 投稿済み\n")
    new = [Decision("aw-1", "try", date(2026, 8, 23), date(2026, 8, 22), "Try me", "https://t"),
           Decision("aw-2", "share", date(2026, 8, 23), date(2026, 8, 22), "Share me", "https://s"),
           Decision("aw-3", "skip_implicit", date(2026, 8, 23), date(2026, 8, 20), "Skip", "https://k")]
    apply_decisions(new, v)
    apply_decisions(new, v)   # 冪等
    assert v.backlog.read_text() == "# Backlog\n\n## 候補\n- [ ] 🧪 **Try me** (追加 2026-08-23) ([link](https://t)) ^aw-1\n\n## 完了\n"
    assert v.outputs.read_text() == "# Outputs\n\n## 投稿待ち\n- [ ] 📣 **Share me** (2026-08-23) ([link](https://s)) ^aw-2\n\n## 投稿済み\n"


def test_sync_decisions_end_to_end(tmp_path):
    v = Vault(tmp_path / "vault")
    v.write_atomic(v.digest_path(date(2026, 8, 20)), DIGEST)
    v.write_atomic(v.backlog, "# Backlog\n\n## 候補\n")
    v.write_atomic(v.outputs, "# Outputs\n\n## 投稿待ち\n\n## 投稿済み\n")
    store = DecisionStore(tmp_path / "decisions.jsonl")
    added = sync_decisions(v, store, date(2026, 8, 23))
    assert {(d.id, d.decision) for d in added} == {("aw-7f3a9c00", "try"), ("aw-aaaaaaaa", "try"), ("aw-0000c0de", "share"), ("aw-1b2c3d00", "skip_implicit")}
    assert "^aw-7f3a9c00" in v.backlog.read_text() and "^aw-0000c0de" in v.outputs.read_text()
    assert sync_decisions(v, store, date(2026, 8, 23)) == []               # 2 回目は何も増えない
