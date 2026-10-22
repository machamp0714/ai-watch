import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ai_watch.claude_runner import ClaudeResult
from ai_watch.models import Item, TriagedItem
from ai_watch.weekly import aggregate, load_week, render_markdown, weekly

END = date(2026, 10, 19)
SIG = {"attention": 0, "tryability": 0, "jp_gap": 0, "relevance": 0}


def _item(i, title, day, source="s1", group="en"):
    return Item(id=i, url=f"https://e.com/{i}", title=title, excerpt="", published_at=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
                metrics={}, lang="en", group=group, source=source, mentions=[source])


def _write_day(data: Path, day: date, rows, held=()):
    """rows: [(Item, category, implication)]"""
    d = data / "work" / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    triaged = [TriagedItem(it.id, cat, 50, SIG, "r", implication=imp).to_dict() for it, cat, imp in rows]
    d.joinpath("triage.json").write_text(json.dumps({
        "items": [it.to_dict() for it, _, _ in rows],
        "outcome": {"mode": "triaged", "triaged": triaged, "cost_usd": 0.1, "error": "", "trends": []},
        "held_items": [it.to_dict() for it in held],
    }), encoding="utf-8")


def _topic_rows(prefix, word, day, n, category="read", sources=("s1", "s2", "s3")):
    return [(_item(f"{prefix}{k}", f"{word} story number {k} about Zeta{k}", day, source=sources[k % len(sources)]), category, "")
            for k in range(n)]


def test_new_topic_is_reported_with_official_origin(tmp_path):
    day = END - timedelta(days=2)
    official = _item("o1", "Introducing Argon reasoning", day - timedelta(days=1), source="google", group="official")
    rows = [(official, "update", "")] + _topic_rows("a", "Argon", day, 8)
    _write_day(tmp_path, day, rows)
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    [topic] = agg["topics"]
    assert topic["label"] == "Argon" and topic["status"] == "new" and topic["count"] == 9
    assert topic["origin"]["id"] == "o1" and [r["id"] for r in topic["official"]] == ["o1"]
    assert agg["updates"][0]["id"] == "o1"


def test_terms_pointing_to_same_items_are_merged(tmp_path):
    rows = [(_item(f"m{k}", f"Meta Muse Spark release note {k}", END, source=f"s{k % 3}"), "read", "") for k in range(9)]
    _write_day(tmp_path, END, rows)
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    assert len(agg["topics"]) == 1
    assert set(agg["topics"][0]["terms"]) == {"meta", "muse", "spark"}


def test_topic_made_of_noise_items_is_dropped(tmp_path):
    _write_day(tmp_path, END, _topic_rows("w", "Windows", END, 10, category="noise"))
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    assert agg["topics"] == []


def test_origin_ignores_old_articles_published_before_the_week(tmp_path):
    old = _item("old", "Argon old teaser", END - timedelta(days=30))
    rows = [(old, "read", "")] + _topic_rows("a", "Argon", END - timedelta(days=3), 8)
    _write_day(tmp_path, END - timedelta(days=3), rows)
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    assert agg["topics"][0]["origin"]["id"] != "old"


def test_rising_and_falling_compare_with_previous_week(tmp_path):
    prev_day = END - timedelta(days=8)
    _write_day(tmp_path, prev_day, _topic_rows("p", "Argon", prev_day, 4) + _topic_rows("h", "Haiku", prev_day, 12))
    _write_day(tmp_path, END, _topic_rows("a", "Argon", END, 9) + _topic_rows("h2", "Haiku", END, 2))
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    assert [(t["label"], t["status"], t["prev_count"]) for t in agg["topics"]] == [("Argon", "rising", 4)]
    assert agg["falling"] == [{"label": "Haiku", "count": 2, "prev_count": 12}]


def test_held_items_count_once_and_missing_days_are_listed(tmp_path):
    held = [_item(f"z{k}", f"Argon note {k}", END, source=f"s{k % 3}") for k in range(3)]
    _write_day(tmp_path, END - timedelta(days=1), [], held=held)
    _write_day(tmp_path, END, [], held=held)
    week = load_week(tmp_path, END)
    assert len(week.items) == 3
    assert week.missing == [END - timedelta(days=n) for n in range(6, 1, -1)]


def test_industry_keeps_implication_and_markdown_flattens_newlines(tmp_path):
    it = _item("i1", "Bank rolls out agents", END)
    _write_day(tmp_path, END, [(it, "industry", "全社導入に進んだ。\n自社でも検討できる。")])
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    assert agg["industry"][0]["implication"].startswith("全社導入")
    md = render_markdown(agg, None)
    assert "  - 示唆: 全社導入に進んだ。 自社でも検討できる。" in md


class FakeRunner:
    def __init__(self, result):
        self.result, self.calls = result, []

    def run(self, prompt, schema, **kw):
        self.calls.append((prompt, schema, kw))
        return self.result


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "weekly.md").write_text("P={{PROFILE}}\nA={{AGGREGATE}}", encoding="utf-8")
    return root


def test_weekly_adds_narrative_from_llm(tmp_path):
    _write_day(tmp_path, END, _topic_rows("a", "Argon", END, 8))
    narrative = {"flow": ["Argon が新しく出た"], "next_moves": [{"kind": "試す", "title": "Argon を触る", "why": "新規の話題"}]}
    runner = FakeRunner(ClaudeResult(True, narrative, 0.2, "success"))
    agg, md, cost = weekly(tmp_path, END, root=_root(tmp_path), profile_md="PROF", runner=runner)
    assert cost == 0.2 and agg["narrative"] == narrative
    assert "P=PROF" in runner.calls[0][0] and '"label": "Argon"' in runner.calls[0][0]
    assert "## 今週の流れ" in md and "**[試す] Argon を触る**" in md


def test_weekly_without_llm_result_still_renders_aggregate(tmp_path):
    _write_day(tmp_path, END, _topic_rows("a", "Argon", END, 8))
    runner = FakeRunner(ClaudeResult(False, None, 0.05, "error", error="budget"))
    agg, md, cost = weekly(tmp_path, END, root=_root(tmp_path), profile_md="", runner=runner)
    assert agg["narrative"] is None and cost == 0.05
    assert "## 今週の流れ" not in md and "| Argon |" in md


def test_steady_topic_is_listed_as_continuing_not_in_table(tmp_path):
    prev_day = END - timedelta(days=8)
    _write_day(tmp_path, prev_day, _topic_rows("p", "Argon", prev_day, 9))
    _write_day(tmp_path, END, _topic_rows("a", "Argon", END, 9))
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    assert agg["topics"] == [] and agg["continuing"] == [{"label": "Argon", "count": 9, "prev_count": 9}]
    md = render_markdown(agg, None)
    assert "前週より伸びた話題はありません" in md and "続いている話題: Argon（9→9）" in md


def test_generic_tech_words_are_not_topics(tmp_path):
    _write_day(tmp_path, END, _topic_rows("w", "Python", END, 10))
    agg = aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7)))
    assert agg["topics"] == []


def test_pipe_in_title_does_not_break_markdown_table(tmp_path):
    rows = [(_item(f"a{k}", f"Argon | review {k}", END, source=f"s{k % 3}"), "read", "") for k in range(8)]
    _write_day(tmp_path, END, rows)
    md = render_markdown(aggregate(load_week(tmp_path, END), load_week(tmp_path, END - timedelta(days=7))), None)
    row = next(l for l in md.splitlines() if l.startswith("| Argon"))
    assert "Argon \\| review" in row and row.count(" | ") == 5
