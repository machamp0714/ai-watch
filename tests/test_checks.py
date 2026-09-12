import json
from datetime import date

import pytest

from ai_watch.checks import CheckFileError, apply_check_events
from ai_watch.decisions import DecisionStore, sync_decisions
from ai_watch.vault import Vault


TODAY = date(2026, 9, 12)
DIGEST_DAY = date(2026, 9, 11)


def _digest() -> str:
    return """---
mode: triaged
---
- [ ] 🧪 **架空ツール** — 理由 ([sample](https://example.com/try)) ^aw-00000001
- [x] 📣 **架空API** ([sample](https://example.com/share)) ^aw-00000002
"""


def _write_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )


def test_apply_check_events_uses_latest_state_and_preserves_markers(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.write_atomic(vault.digest_path(DIGEST_DAY), _digest())
    checks = tmp_path / "checks"
    _write_events(
        checks / f"{DIGEST_DAY.isoformat()}.jsonl",
        [
            {
                "id": "aw-00000001",
                "category": "try",
                "checked": False,
                "timestamp": "2026-09-11T01:00:00Z",
            },
            {
                "id": "aw-00000001",
                "category": "try",
                "checked": True,
                "timestamp": "2026-09-11T02:00:00Z",
            },
            {
                "id": "aw-00000002",
                "category": "share",
                "checked": False,
                "timestamp": "2026-09-11T03:00:00Z",
            },
        ],
    )

    changed = apply_check_events(vault, checks, TODAY)

    md = vault.read_digest(DIGEST_DAY)
    assert changed == 1
    assert "- [x] 🧪 **架空ツール**" in md
    assert "- [ ] 📣 **架空API**" in md
    assert "^aw-00000001" in md and "^aw-00000002" in md


def test_applied_checks_flow_through_existing_decision_sync(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.write_atomic(vault.digest_path(DIGEST_DAY), _digest())
    checks = tmp_path / "checks"
    _write_events(
        checks / f"{DIGEST_DAY.isoformat()}.jsonl",
        [
            {
                "id": "aw-00000001",
                "category": "try",
                "checked": True,
                "timestamp": "2026-09-11T02:00:00Z",
            }
        ],
    )
    store = DecisionStore(tmp_path / "data" / "decisions.jsonl")

    apply_check_events(vault, checks, TODAY)
    decisions = sync_decisions(vault, store, TODAY)

    assert {(decision.id, decision.decision) for decision in decisions} == {
        ("aw-00000001", "try"),
        ("aw-00000002", "share"),
    }


def test_category_mismatch_cannot_check_another_kind(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.write_atomic(vault.digest_path(DIGEST_DAY), _digest())
    checks = tmp_path / "checks"
    _write_events(
        checks / f"{DIGEST_DAY.isoformat()}.jsonl",
        [
            {
                "id": "aw-00000001",
                "category": "share",
                "checked": True,
                "timestamp": "2026-09-11T02:00:00Z",
            }
        ],
    )

    assert apply_check_events(vault, checks, TODAY) == 0
    assert "- [ ] 🧪 **架空ツール**" in vault.read_digest(DIGEST_DAY)


def test_invalid_check_line_stops_without_exposing_body(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.write_atomic(vault.digest_path(DIGEST_DAY), _digest())
    checks = tmp_path / "checks"
    path = checks / f"{DIGEST_DAY.isoformat()}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("private-marker {broken\n", encoding="utf-8")

    with pytest.raises(CheckFileError) as captured:
        apply_check_events(vault, checks, TODAY)

    assert "private-marker" not in str(captured.value)
    assert vault.read_digest(DIGEST_DAY) == _digest()
