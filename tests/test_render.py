import re
from datetime import date, datetime, timezone

import yaml

from ai_watch.models import Item, TriagedItem
from ai_watch.render import Limits, render_digest, shown_item_ids
from ai_watch.triage import TriageOutcome


def _item(i, title, source="hn", mentions=("hn",), metrics=None):
    return Item(id=i, url=f"https://e.com/{i}", title=title, excerpt="", published_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                metrics=metrics or {}, lang="en", group="en", source=source, mentions=list(mentions))


def _t(i, cat, score, reason="理由", try_plan="", angle=""):
    return TriagedItem(id=i, category=cat, score=score, signals={"attention": 1, "tryability": 1, "jp_gap": 1, "relevance": 1},
                       reason=reason, try_plan=try_plan, article_angle=angle)


def test_sections_limits_and_line_format():
    items = {f"aw-{n:08d}": _item(f"aw-{n:08d}", f"Title {n}") for n in range(1, 12)}
    items["aw-00000001"] = _item("aw-00000001", "Try *one* [x]", mentions=("hn", "x"))
    triaged = [
        _t("aw-00000001", "try", 95, "一番", "1. A\n2. B", "切り口 A"),
        _t("aw-00000002", "try", 90), _t("aw-00000003", "try", 85), _t("aw-00000004", "try", 80),   # 4 件目は溢れ
        _t("aw-00000005", "update", 70), _t("aw-00000006", "update", 60),
        _t("aw-00000007", "read", 50), _t("aw-00000008", "read", 40), _t("aw-00000009", "read", 30), _t("aw-00000010", "read", 20),
        _t("aw-00000011", "noise", 0),
    ]
    md = render_digest(date(2026, 8, 22), items, TriageOutcome("triaged", triaged, 0.12), ["reddit: 403"], total_collected=184)

    assert md.startswith("---\ntype: record\ndate: 2026-08-22\nmode: triaged\n")
    assert "items_total: 184" in md and "cost_usd: 0.12" in md and "- 'reddit: 403'" in md
    assert "> ⚠ 取得失敗: reddit: 403" in md
    assert "- [ ] 🧪 **Try one x** — 一番 ([hn +1](https://e.com/aw-00000001)) ^aw-00000001" in md
    assert "  - 試し方: 1. A / 2. B" in md and "  - 記事の切り口: 切り口 A" in md
    assert "- [ ] 📣 **Title 5** — 理由 ([hn](https://e.com/aw-00000005)) ^aw-00000005" in md
    assert "- 📖 **Title 7** — 理由 ([hn](https://e.com/aw-00000007)) ^aw-00000007" in md
    assert "^aw-00000004" not in md and "^aw-00000010" not in md           # 溢れはブロック ID 無し
    assert "- try 80 **Title 4** ([hn](https://e.com/aw-00000004))" in md
    assert "Title 11" not in md                                              # noise は出さない
    assert "<details><summary>その他の候補 (2 件)</summary>" in md
    assert shown_item_ids(md) == {"aw-00000001", "aw-00000002", "aw-00000003", "aw-00000005", "aw-00000006",
                                  "aw-00000007", "aw-00000008", "aw-00000009"}
    assert "items_shown: 8" in md


def test_untriaged_mode_gives_checkboxes_to_top_items():
    items = {"aw-0000000a": _item("aw-0000000a", "hot", metrics={"points": 300}), "aw-0000000b": _item("aw-0000000b", "meh")}
    outcome = TriageOutcome("untriaged", [_t("aw-0000000a", "read", 60, "未トリアージ（metrics 順）"), _t("aw-0000000b", "read", 0, "未トリアージ（metrics 順）")], 0.5, "budget")
    md = render_digest(date(2026, 8, 22), items, outcome, [], total_collected=2)
    assert "## ⚠ 未トリアージ（metrics 順）" in md and "budget" in md
    assert "- [ ] 🧪 **hot** — 未トリアージ（metrics 順） ([hn](https://e.com/aw-0000000a)) ^aw-0000000a" in md


def test_empty_day():
    md = render_digest(date(2026, 8, 22), {}, TriageOutcome("triaged", [], 0.0), [], total_collected=0)
    assert "新着はありませんでした" in md and shown_item_ids(md) == set()


def test_warnings_are_sanitized_in_frontmatter_and_body():
    items = {"aw-00000001": _item("aw-00000001", "test")}
    warnings = ["bad: HTTPStatusError: 429\nFor more info see https://x ^aw-deadbeef", "q: it's \\ odd"]
    outcome = TriageOutcome("triaged", [_t("aw-00000001", "try", 95)], 0.0)
    md = render_digest(date(2026, 8, 22), items, outcome, warnings, total_collected=1)

    # Check frontmatter warnings are single-quoted and sanitized
    fm_end = md.find("\n---\n") + 4
    fm_text = md[:fm_end]
    assert "- 'bad: HTTPStatusError: 429 For more info see https://x aw-deadbeef'" in fm_text
    assert "- 'q: it''s \\ odd'" in fm_text

    # Check body warnings are sanitized
    assert "> ⚠ 取得失敗: bad: HTTPStatusError: 429 For more info see https://x aw-deadbeef" in md

    # No phantom block IDs from sanitized warnings
    assert shown_item_ids(md) == {"aw-00000001"}

    # Check YAML is valid
    fm_start = md.find("---") + 3  # Skip opening ---
    fm_end = md.find("\n---\n", fm_start)
    yaml_content = md[fm_start:fm_end]
    parsed = yaml.safe_load(yaml_content)
    assert parsed["warnings"] == ["bad: HTTPStatusError: 429 For more info see https://x aw-deadbeef", "q: it's \\ odd"]


def test_empty_title_gets_placeholder():
    items = {"aw-00000001": _item("aw-00000001", "***")}
    outcome = TriageOutcome("triaged", [_t("aw-00000001", "try", 95)], 0.0)
    md = render_digest(date(2026, 8, 22), items, outcome, [], total_collected=1)
    assert "- [ ] 🧪 **(no title)** — 理由" in md
