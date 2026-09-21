import json
from datetime import date, datetime, timezone
from pathlib import Path

import jsonschema

from ai_watch.models import Item, TriagedItem
from ai_watch.render import render_digest, render_digest_data, shown_item_ids
from ai_watch.trends import Trend, baseline_terms, detect_trends
from ai_watch.triage import TriageOutcome, build_prompt, promote_popular, promote_trends


def _item(i, title, source="s", metrics=None, mentions=None):
    return Item(id=i, url=f"https://e.com/{i}", title=title, excerpt="本文の一行目\n二行目",
                published_at=datetime(2026, 9, 18, tzinfo=timezone.utc), metrics=metrics or {},
                lang="ja", group="jp", source=source, mentions=list(mentions or [source]))


def _noise(i):
    return TriagedItem(id=i, category="noise", score=0,
                       signals={"attention": 0, "tryability": 0, "jp_gap": 0, "relevance": 0}, reason="")


JEV_TITLES = [
    ("zenn-llm", "TypeSafeのJevを正しく驚く"),
    ("zenn-llm", "Jevでハーネスエンジニアリング"),
    ("reddit-localllama", "Still on the Jev waitlist? I hosted OpenJev"),
    ("reddit-localllama", "LocalJev?"),
    ("latent-space", "[AINews] Jev: a System One Model"),
    ("hatena-it-hot", "jev 同士に五目並べで対戦させた"),
]


def _jev_items():
    return [_item(f"aw-{n:08x}", title, source) for n, (source, title) in enumerate(JEV_TITLES)]


def test_detect_trends_finds_new_name_across_sources_including_compounds():
    items = _jev_items() + [_item("aw-000000ff", "Claude Code の hooks 入門", "zenn-claudecode")]
    trends = detect_trends(items)
    assert [t.term for t in trends] == ["jev"]
    assert trends[0].label == "Jev"
    assert len(trends[0].item_ids) == 6          # LocalJev / OpenJev も含む
    assert trends[0].sources == ["hatena-it-hot", "latent-space", "reddit-localllama", "zenn-llm"]


def test_detect_trends_ignores_collection_targets_and_common_words():
    titles = [("a", "Claude Code tips for you"), ("b", "Using Claude Code with hooks"), ("c", "Claude Code is great"),
              ("a", "My Claude Code setup"), ("b", "Claude Code usage limits"), ("c", "What Claude Code can do")]
    items = [_item(f"aw-{n:08x}", t, s) for n, (s, t) in enumerate(titles)]
    assert detect_trends(items) == []


def test_detect_trends_requires_multiple_sources():
    items = [_item(f"aw-{n:08x}", f"Jev の記事 {n}", "zenn-llm") for n in range(8)]
    assert detect_trends(items) == []


def test_detect_trends_merges_overlapping_terms():
    titles = [("a", "TypeSafe Jev launch"), ("b", "TypeSafe Jev pricing"), ("c", "TypeSafe Jev review"),
              ("a", "TypeSafe Jev benchmark"), ("b", "TypeSafe Jev demo"), ("c", "Jev clone")]
    items = [_item(f"aw-{n:08x}", t, s) for n, (s, t) in enumerate(titles)]
    assert [t.term for t in detect_trends(items)] == ["jev"]


def test_build_prompt_lists_trends():
    trend = Trend(term="jev", label="Jev", item_ids=["aw-1", "aw-2"], sources=["a", "b"])
    p = build_prompt("T={{TRENDS}}", [], "", [], date(2026, 9, 18), [trend])
    assert "T=- Jev: 2 件・2 ソース（a, b） ids=aw-1, aw-2" in p
    assert "目立った話題クラスタはありません" in build_prompt("{{TRENDS}}", [], "", [], date(2026, 9, 18))


def test_promote_popular_rescues_high_likes_from_noise():
    items = [_item("aw-1", "人気", metrics={"likes": 140}), _item("aw-2", "普通", metrics={"likes": 3})]
    out = {t.id: t for t in promote_popular([_noise("aw-1"), _noise("aw-2")], items)}
    assert out["aw-1"].category == "read" and out["aw-1"].score == 45 and out["aw-1"].signals["attention"] == 3
    assert out["aw-1"].summary == "本文の一行目"
    assert out["aw-2"].category == "noise"


def test_promote_popular_skips_keywordless_hn_only_items():
    items = [_item("aw-1", "Disney+ ads", "hn-top", metrics={"points": 900}),
             _item("aw-2", "New coding agent", "hn", metrics={"points": 900}, mentions=["hn", "hn-top"])]
    out = {t.id: t for t in promote_popular([_noise("aw-1"), _noise("aw-2")], items)}
    assert out["aw-1"].category == "noise"
    assert out["aw-2"].category == "read"


def test_promote_trends_rescues_top_representatives_only():
    items = _jev_items()
    items[1] = _item(items[1].id, items[1].title, "zenn-llm", metrics={"likes": 65})
    items[0] = _item(items[0].id, items[0].title, "zenn-llm", metrics={"likes": 140})
    trends = detect_trends(items)
    triaged = [_noise(it.id) for it in items]
    out = {t.id: t for t in promote_trends(triaged, items, trends)}
    promoted = [i for i, t in out.items() if t.category == "read"]
    assert sorted(promoted) == sorted([items[0].id, items[1].id])
    assert out[items[0].id].score == 50


def test_promote_trends_keeps_llm_judgement_when_already_shown():
    items = _jev_items()
    trends = detect_trends(items)
    triaged = [_noise(it.id) for it in items]
    triaged[3] = TriagedItem(id=items[3].id, category="try", score=70, signals=triaged[3].signals, reason="r")
    out = {t.id: t for t in promote_trends(triaged, items, trends)}
    assert out[items[3].id].category == "try" and out[items[3].id].score == 70
    assert sum(1 for t in out.values() if t.category != "noise") == 2


def test_outcome_roundtrip_keeps_trends_and_accepts_old_format():
    trend = Trend(term="jev", label="Jev", item_ids=["aw-1"], sources=["a"])
    out = TriageOutcome("triaged", [], 0.1, trends=[trend])
    assert TriageOutcome.from_dict(out.to_dict()).trends == [trend]
    assert TriageOutcome.from_dict({"mode": "triaged", "triaged": [], "cost_usd": 0}).trends == []


def test_render_shows_trend_section_without_block_ids():
    items_list = _jev_items()
    items = {it.id: it for it in items_list}
    trends = detect_trends(items_list)
    triaged = [_noise(it.id) for it in items_list]
    triaged[4] = TriagedItem(id=items_list[4].id, category="read", score=60, signals=triaged[4].signals,
                             reason="r", summary="System One モデルの紹介")
    outcome = TriageOutcome("triaged", triaged, 0.1, trends=trends)
    md = render_digest(date(2026, 9, 18), items, outcome, [], total_collected=6)
    data = render_digest_data(date(2026, 9, 18), items, outcome, [], total_collected=6)

    body = md.split("## 🔥 今日の話題", 1)[1].split("## 🧪", 1)[0]
    assert "- **Jev**（6 件・4 ソース）" in body
    assert "[[AINews] Jev: a System One Model]" not in body     # [] は落とす
    first = body.strip().splitlines()[1]
    assert "AINews Jev" in first and "System One モデルの紹介" in first
    assert len(body.strip().splitlines()) == 1 + 3
    assert shown_item_ids(md) == {items_list[4].id}
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "digest.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)
    assert data["trends"][0]["label"] == "Jev" and data["trends"][0]["count"] == 6
    assert data["trends"][0]["items"][0]["summary"] == ["System One モデルの紹介"]


def test_render_without_trends_has_no_section():
    md = render_digest(date(2026, 9, 18), {}, TriageOutcome("triaged", [], 0.0), [], total_collected=0)
    assert "今日の話題" not in md


def _fable_day(prefix, n=6):
    return [_item(f"aw-{prefix}{k:06x}", f"Fable 5.1 の話 {k}", ["a", "b", "c"][k % 3]) for k in range(n)]


def test_baseline_excludes_perennial_names_but_keeps_spikes():
    past = [_fable_day(f"{d:02x}") for d in range(4)] + [[]] * 3       # 4/7 日で話題 → 平均 24/7 件
    baseline = baseline_terms(past)
    assert set(baseline) == {"fable"} and round(baseline["fable"], 2) == round(24 / 7, 2)

    today = _fable_day("ff") + _jev_items()
    assert [t.term for t in detect_trends(today, baseline=baseline)] == ["jev"]   # 6 件 < 2 × 3.43
    spike = _fable_day("fe", n=9) + _jev_items()
    assert "fable" in {t.term for t in detect_trends(spike, baseline={"fable": 3.0})}   # 9 ≥ 2 × 3
    assert "fable" not in {t.term for t in detect_trends(spike, baseline={"fable": 5.0})}


def test_baseline_needs_min_days():
    past = [_fable_day("01"), _fable_day("02")] + [[]] * 5
    assert baseline_terms(past) == {}


def test_past_items_reads_previous_days_and_skips_missing_or_broken(tmp_path):
    from ai_watch.pipeline import _past_items

    def write(day, payload):
        d = tmp_path / "work" / day
        d.mkdir(parents=True)
        (d / "triage.json").write_text(payload)

    write("2026-09-19", json.dumps({"items": [_item("aw-00000001", "Jev").to_dict()], "outcome": {}}))
    write("2026-09-17", "{broken")
    write("2026-09-20", json.dumps({"items": [_item("aw-00000002", "today").to_dict()]}))   # 当日は含めない
    out = _past_items(tmp_path, date(2026, 9, 20), days=3)
    assert [[it.id for it in items] for items in out] == [["aw-00000001"]]
