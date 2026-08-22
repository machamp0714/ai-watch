from datetime import date, datetime, timezone

from ai_watch.models import Decision, Item, RawItem, raw_from_dict, raw_to_dict


def test_item_roundtrip():
    it = Item(
        id="aw-0123abcd", url="https://example.com/a", title="T", excerpt="E",
        published_at=datetime(2026, 8, 22, 1, 2, 3, tzinfo=timezone.utc),
        metrics={"points": 3}, lang="en", group="en", source="hn", mentions=["hn"],
    )
    d = it.to_dict()
    assert d["published_at"] == "2026-08-22T01:02:03+00:00"
    assert Item.from_dict(d) == it


def test_item_roundtrip_without_date():
    it = Item(id="aw-0", url="u", title="t", excerpt="", published_at=None,
              metrics={}, lang="ja", group="jp", source="zenn", mentions=["zenn"])
    assert Item.from_dict(it.to_dict()) == it


def test_raw_roundtrip():
    r = RawItem(source="s", url="https://x", title="t", excerpt="e",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc), metrics={"likes": 1}, lang="ja")
    assert raw_from_dict(raw_to_dict(r)) == r


def test_decision_roundtrip():
    d = Decision(id="aw-1", decision="try", date=date(2026, 8, 23), digest_date=date(2026, 8, 22),
                 title="T", url="https://u")
    assert Decision.from_dict(d.to_dict()) == d
