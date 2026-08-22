import json
from datetime import date, datetime, timezone
from pathlib import Path

from ai_watch.claude_runner import ClaudeResult
from ai_watch.models import Decision, Item
from ai_watch.triage import TriageOutcome, build_prompt, fallback_rank, triage


def _item(i, title, metrics=None, mentions=("s",), group="en"):
    return Item(id=i, url=f"https://e.com/{i}", title=title, excerpt="x" * 700, published_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                metrics=metrics or {}, lang="en", group=group, source="s", mentions=list(mentions))


def _root(tmp_path: Path) -> Path:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "triage.md").write_text("D={{DATE}}\nP={{PROFILE}}\nDEC={{DECISIONS}}\nI={{ITEMS}}")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "triage.schema.json").write_text(json.dumps({"type": "object"}))
    return tmp_path


def test_build_prompt_substitutes_and_truncates_excerpt():
    items = [_item("aw-1", "One", {"points": 100}, ("hn", "x"))]
    decs = [Decision(id="aw-9", decision="try", date=date(2026, 8, 20), digest_date=date(2026, 8, 19), title="Old", url="https://o")]
    p = build_prompt("D={{DATE}}\nP={{PROFILE}}\nDEC={{DECISIONS}}\nI={{ITEMS}}", items, "PROF", decs, date(2026, 8, 22))
    assert "D=2026-08-22" in p and "P=PROF" in p
    assert "- [try] Old (https://o)" in p
    payload = json.loads(p.split("I=", 1)[1])
    assert payload[0]["id"] == "aw-1" and payload[0]["mentions"] == ["hn", "x"] and payload[0]["metrics"] == {"points": 100}
    assert len(payload[0]["excerpt"]) <= 600


def test_build_prompt_without_decisions():
    p = build_prompt("DEC={{DECISIONS}}", [], "", [], date(2026, 8, 22))
    assert "DEC=（まだ判断履歴はありません）" in p


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, prompt, schema, **kw):
        self.calls.append((prompt, schema, kw))
        return self.result


def test_triage_success_marks_missing_as_noise_and_clamps(tmp_path):
    items = [_item("aw-1", "A"), _item("aw-2", "B"), _item("aw-3", "C")]
    data = {"items": [
        {"id": "aw-1", "category": "try", "score": 90, "signals": {"attention": 3, "tryability": 3, "jp_gap": 2, "relevance": 3},
         "reason": "r", "try_plan": "p", "article_angle": "a"},
        {"id": "aw-2", "category": "read", "score": 140, "signals": {"attention": 1, "tryability": 0, "jp_gap": 1, "relevance": 2}, "reason": "r2"},
        {"id": "aw-unknown", "category": "try", "score": 50, "signals": {"attention": 0, "tryability": 0, "jp_gap": 0, "relevance": 0}, "reason": "ghost"},
    ]}
    runner = FakeRunner(ClaudeResult(True, data, 0.12, "success"))
    out = triage(items, profile_md="P", decisions=[], runner=runner, root=_root(tmp_path), day=date(2026, 8, 22))
    assert out.mode == "triaged" and out.cost_usd == 0.12
    by_id = {t.id: t for t in out.triaged}
    assert set(by_id) == {"aw-1", "aw-2", "aw-3"}                       # 未知 id は捨て、欠けた id は noise
    assert by_id["aw-1"].category == "try" and by_id["aw-1"].try_plan == "p"
    assert by_id["aw-2"].score == 100                                    # clamp
    assert by_id["aw-3"].category == "noise"
    assert runner.calls[0][2]["budget_usd"] == 2.0


def test_triage_downgrades_non_official_update_category(tmp_path):
    items = [_item("aw-1", "A", group="official"), _item("aw-2", "B", group="en")]
    data = {"items": [
        {"id": "aw-1", "category": "update", "score": 80,
         "signals": {"attention": 1, "tryability": 0, "jp_gap": 0, "relevance": 1}, "reason": "r1"},
        {"id": "aw-2", "category": "update", "score": 70,
         "signals": {"attention": 1, "tryability": 0, "jp_gap": 0, "relevance": 1}, "reason": "r2"},
    ]}
    runner = FakeRunner(ClaudeResult(True, data, 0.1, "success"))
    out = triage(items, profile_md="", decisions=[], runner=runner, root=_root(tmp_path), day=date(2026, 8, 22))
    by_id = {t.id: t for t in out.triaged}
    assert by_id["aw-1"].category == "update"     # official のまま
    assert by_id["aw-2"].category == "read"        # 公式以外の update は read に格下げ


def test_triage_failure_falls_back(tmp_path):
    items = [_item("aw-1", "low"), _item("aw-2", "hot", {"points": 300}, ("hn", "reddit", "x"))]
    runner = FakeRunner(ClaudeResult(False, None, 0.5, "error_max_budget_usd", error="budget"))
    out = triage(items, profile_md="", decisions=[], runner=runner, root=_root(tmp_path), day=date(2026, 8, 22))
    assert out.mode == "untriaged" and out.error == "budget" and out.cost_usd == 0.5
    assert [t.id for t in out.triaged] == ["aw-2", "aw-1"]
    assert all(t.category == "read" for t in out.triaged)


def test_triage_empty_items_does_not_call_runner(tmp_path):
    runner = FakeRunner(None)
    out = triage([], profile_md="", decisions=[], runner=runner, root=_root(tmp_path), day=date(2026, 8, 22))
    assert out.mode == "triaged" and out.triaged == [] and runner.calls == []


def test_outcome_roundtrip():
    o = TriageOutcome("untriaged", fallback_rank([_item("aw-1", "t", {"likes": 2})]), 0.1, "e")
    assert TriageOutcome.from_dict(o.to_dict()) == o


def test_parse_triage_coerces_bad_category_and_non_list_items():
    from ai_watch.triage import parse_triage
    # Non-list items should be coerced to empty list
    result = parse_triage({"items": "oops"}, ["aw-1"])
    assert len(result) == 1 and result[0].id == "aw-1" and result[0].category == "noise"
    # Bad category should be coerced to "noise"
    result = parse_triage({"items": [{"id": "aw-1", "category": "banana", "score": 5, "signals": {}, "reason": "r"}]}, ["aw-1"])
    assert result[0].category == "noise" and result[0].score == 5


def test_parse_triage_downgrades_non_official_update_to_read():
    from ai_watch.triage import parse_triage
    data = {"items": [{"id": "aw-1", "category": "update", "score": 80, "signals": {}, "reason": "r"}]}
    result = parse_triage(data, ["aw-1"], groups={"aw-1": "en"})
    assert result[0].category == "read"


def test_parse_triage_keeps_official_update():
    from ai_watch.triage import parse_triage
    data = {"items": [{"id": "aw-1", "category": "update", "score": 80, "signals": {}, "reason": "r"}]}
    result = parse_triage(data, ["aw-1"], groups={"aw-1": "official"})
    assert result[0].category == "update"


def test_parse_triage_without_groups_does_not_downgrade():
    from ai_watch.triage import parse_triage
    data = {"items": [{"id": "aw-1", "category": "update", "score": 80, "signals": {}, "reason": "r"}]}
    result = parse_triage(data, ["aw-1"])
    assert result[0].category == "update"


def test_fallback_rank_handles_negative_metrics_and_no_mentions():
    result = fallback_rank([_item("aw-1", "t", {"points": -5}, ())])
    assert result[0].score == 0 and 0 <= result[0].score <= 100
