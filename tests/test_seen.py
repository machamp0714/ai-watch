from datetime import date

from ai_watch.models import Item
from ai_watch.seen import SeenStore


def _item(i: str) -> Item:
    return Item(id=i, url=f"https://e.com/{i}", title=i, excerpt="", published_at=None,
                metrics={}, lang="en", group="en", source="s", mentions=["s"])


def test_new_then_seen(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as s:
        today, tomorrow = date(2026, 8, 22), date(2026, 8, 23)
        a, b = _item("aw-a"), _item("aw-b")
        assert s.filter_new([a, b], today) == [a, b]
        s.mark([a, b], today, shown_ids={"aw-a"}, categories={"aw-a": "try", "aw-b": "noise"})
        assert s.filter_new([a, b], today) == [a, b]       # 同日の再実行（--from triage）では除外しない
        assert s.filter_new([a, b], tomorrow) == []
        assert s.is_new("aw-a", tomorrow) is False


def test_reappears_after_window(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as s:
        a = _item("aw-a")
        s.mark([a], date(2026, 7, 1), shown_ids=set(), categories={})
        assert s.is_new("aw-a", date(2026, 7, 20)) is False
        assert s.is_new("aw-a", date(2026, 8, 5)) is True   # 30 日超


def test_mark_is_idempotent_and_keeps_first_seen(tmp_path):
    p = tmp_path / "seen.sqlite"
    with SeenStore(p) as s:
        a = _item("aw-a")
        s.mark([a], date(2026, 8, 1), shown_ids=set(), categories={})
        s.mark([a], date(2026, 8, 2), shown_ids={"aw-a"}, categories={"aw-a": "read"})
        row = s.get("aw-a")
        assert row["first_seen"] == "2026-08-01"
        assert row["shown_on"] == "2026-08-02"
        assert row["category"] == "read"
