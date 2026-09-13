import sqlite3
from datetime import date

from ai_watch.models import Item
from ai_watch.seen import SeenStore


def _item(
    i: str,
    *,
    likes: int | None = None,
    bookmarks: int | None = None,
    source: str = "s",
    mentions: list[str] | None = None,
    url: str | None = None,
) -> Item:
    metrics = {}
    if likes is not None:
        metrics["likes"] = likes
    if bookmarks is not None:
        metrics["bookmarks"] = bookmarks
    return Item(id=i, url=url or f"https://e.com/{i}", title=i, excerpt="", published_at=None,
                metrics=metrics, lang="en", group="en",
                source=source, mentions=mentions or [source])


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


def test_unshown_noise_reappears_when_zenn_likes_cross_a_new_tier(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        first = date(2026, 9, 1)
        item = _item("aw-zenn", likes=2, source="zenn")
        store.mark([item], first, shown_ids=set(), categories={item.id: "noise"})

        assert store.filter_new(
            [_item("aw-zenn", likes=9, source="zenn")],
            date(2026, 9, 2),
            popularity_sources={"zenn"},
        ) == []

        ten_likes = _item("aw-zenn", likes=10, source="zenn")
        assert store.filter_new(
            [ten_likes], date(2026, 9, 2), popularity_sources={"zenn"}
        ) == [ten_likes]
        store.mark([ten_likes], date(2026, 9, 2), shown_ids=set(), categories={ten_likes.id: "noise"})

        assert store.filter_new(
            [_item("aw-zenn", likes=29, source="zenn")],
            date(2026, 9, 3),
            popularity_sources={"zenn"},
        ) == []
        thirty_likes = _item("aw-zenn", likes=30, source="zenn")
        assert store.filter_new(
            [thirty_likes],
            date(2026, 9, 3),
            popularity_sources={"zenn"},
        ) == [thirty_likes]
        store.mark(
            [thirty_likes],
            date(2026, 9, 3),
            shown_ids=set(),
            categories={thirty_likes.id: "noise"},
        )

        assert store.filter_new(
            [_item("aw-zenn", likes=99, source="zenn")],
            date(2026, 9, 4),
            popularity_sources={"zenn"},
        ) == []
        assert len(store.filter_new(
            [_item("aw-zenn", likes=100, source="zenn")],
            date(2026, 9, 4),
            popularity_sources={"zenn"},
        )) == 1


def test_popularity_growth_does_not_repeat_shown_or_unconfigured_sources(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        first = date(2026, 9, 1)
        shown = _item("aw-shown", likes=2, source="zenn")
        other = _item("aw-other", likes=2, source="other")
        store.mark([shown], first, shown_ids={shown.id}, categories={shown.id: "read"})
        store.mark([other], first, shown_ids=set(), categories={other.id: "noise"})

        candidates = store.filter_new(
            [
                _item("aw-shown", likes=100, source="zenn"),
                _item("aw-other", likes=100, source="other"),
            ],
            date(2026, 9, 2),
            popularity_sources={"zenn"},
        )

        assert candidates == []


def test_recheck_tier_uses_likes_instead_of_another_larger_metric(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        first = _item("aw-zenn", likes=2, bookmarks=50, source="zenn")
        store.mark(
            [first],
            date(2026, 9, 1),
            shown_ids=set(),
            categories={first.id: "noise"},
        )
        grown = _item("aw-zenn", likes=10, bookmarks=50, source="zenn")

        assert store.filter_new(
            [grown],
            date(2026, 9, 2),
            popularity_sources={"zenn"},
        ) == [grown]


def test_merged_item_rechecks_when_any_mention_is_a_popularity_source(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        first = _item(
            "aw-merged",
            likes=2,
            source="hatena",
            mentions=["hatena", "zenn"],
        )
        store.mark(
            [first],
            date(2026, 9, 1),
            shown_ids=set(),
            categories={first.id: "noise"},
        )
        grown = _item(
            "aw-merged",
            likes=10,
            source="hatena",
            mentions=["hatena", "zenn"],
        )

        assert store.filter_new(
            [grown],
            date(2026, 9, 2),
            popularity_sources={"zenn"},
        ) == [grown]


def test_shown_popularity_source_does_not_reappear_after_normal_seen_window(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        shown = _item("aw-shown", likes=2, source="zenn")
        store.mark(
            [shown],
            date(2026, 9, 1),
            shown_ids={shown.id},
            categories={shown.id: "read"},
        )
        grown = _item("aw-shown", likes=100, source="zenn")

        assert store.filter_new(
            [grown],
            date(2026, 10, 2),
            popularity_sources={"zenn"},
        ) == []


def test_unshown_zenn_does_not_repeat_in_the_same_tier_after_normal_seen_window(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        first = _item("aw-zenn", likes=10, source="zenn")
        store.mark(
            [first],
            date(2026, 9, 1),
            shown_ids=set(),
            categories={first.id: "noise"},
        )

        assert store.filter_new(
            [_item("aw-zenn", likes=29, source="zenn")],
            date(2026, 10, 2),
            popularity_sources={"zenn"},
        ) == []


def test_zenn_recheck_candidates_are_unshown_noise_within_seven_days(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        tracked = _item(
            "aw-tracked",
            likes=2,
            source="zenn-codex",
            url="https://zenn.dev/sample/articles/tracked",
        )
        expired = _item(
            "aw-expired",
            likes=2,
            source="zenn-codex",
            url="https://zenn.dev/sample/articles/expired",
        )
        shown = _item(
            "aw-shown-zenn",
            likes=2,
            source="zenn-codex",
            url="https://zenn.dev/sample/articles/shown",
        )
        popular = _item(
            "aw-popular",
            likes=100,
            source="zenn-codex",
            url="https://zenn.dev/sample/articles/popular",
        )
        other = _item(
            "aw-other-host",
            likes=2,
            source="zenn-codex",
            url="https://example.com/articles/other",
        )
        store.mark(
            [tracked],
            date(2026, 9, 6),
            shown_ids=set(),
            categories={tracked.id: "noise"},
            popularity_sources={"zenn-codex"},
        )
        store.mark(
            [expired],
            date(2026, 9, 5),
            shown_ids=set(),
            categories={expired.id: "noise"},
            popularity_sources={"zenn-codex"},
        )
        store.mark(
            [shown],
            date(2026, 9, 6),
            shown_ids={shown.id},
            categories={shown.id: "read"},
            popularity_sources={"zenn-codex"},
        )
        store.mark(
            [popular, other],
            date(2026, 9, 6),
            shown_ids=set(),
            categories={popular.id: "noise", other.id: "noise"},
            popularity_sources={"zenn-codex"},
        )

        candidates = store.zenn_recheck_candidates(date(2026, 9, 13))

        assert [(candidate.id, candidate.url, candidate.source_id) for candidate in candidates] == [
            (
                "aw-tracked",
                "https://zenn.dev/sample/articles/tracked",
                "zenn-codex",
            )
        ]


def test_existing_seen_database_is_migrated_for_popularity_source(tmp_path):
    path = tmp_path / "seen.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE seen (
              id TEXT PRIMARY KEY,
              url TEXT NOT NULL,
              title TEXT,
              first_seen TEXT NOT NULL,
              shown_on TEXT,
              category TEXT,
              max_points INTEGER
            );
            """
        )

    with SeenStore(path) as store:
        item = _item(
            "aw-migrated",
            likes=2,
            source="zenn-llm",
            url="https://zenn.dev/sample/articles/migrated",
        )
        store.mark(
            [item],
            date(2026, 9, 12),
            shown_ids=set(),
            categories={item.id: "noise"},
            popularity_sources={"zenn-llm"},
        )

        assert store.zenn_recheck_candidates(date(2026, 9, 13))[0].source_id == "zenn-llm"
        row = store.get(item.id)
        assert row["max_likes"] == 2


def test_stored_popularity_source_prevents_repeat_when_later_mention_is_only_other_source(tmp_path):
    with SeenStore(tmp_path / "seen.sqlite") as store:
        shown = _item(
            "aw-cross-source",
            likes=10,
            source="zenn-llm",
            url="https://zenn.dev/sample/articles/cross-source",
        )
        store.mark(
            [shown],
            date(2026, 9, 1),
            shown_ids={shown.id},
            categories={shown.id: "read"},
            popularity_sources={"zenn-llm"},
        )
        from_hatena = _item(
            "aw-cross-source",
            source="hatena",
            url="https://zenn.dev/sample/articles/cross-source",
        )

        assert store.filter_new(
            [from_hatena],
            date(2026, 10, 2),
            popularity_sources={"zenn-llm"},
        ) == []


def test_legacy_zenn_row_without_popularity_source_does_not_repeat(tmp_path):
    path = tmp_path / "seen.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE seen (
              id TEXT PRIMARY KEY,
              url TEXT NOT NULL,
              title TEXT,
              first_seen TEXT NOT NULL,
              shown_on TEXT,
              category TEXT,
              max_points INTEGER
            );
            INSERT INTO seen VALUES (
              'aw-legacy-zenn',
              'https://zenn.dev/sample/articles/legacy',
              '移行前の架空記事',
              '2026-09-01',
              '2026-09-01',
              'read',
              NULL
            );
            """
        )

    with SeenStore(path) as store:
        from_hatena = _item(
            "aw-legacy-zenn",
            source="hatena",
            url="https://zenn.dev/sample/articles/legacy",
        )

        assert store.filter_new(
            [from_hatena],
            date(2026, 10, 2),
            popularity_sources={"zenn-llm"},
        ) == []


def test_legacy_max_points_is_not_treated_as_zenn_likes(tmp_path):
    path = tmp_path / "seen.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE seen (
              id TEXT PRIMARY KEY,
              url TEXT NOT NULL,
              title TEXT,
              first_seen TEXT NOT NULL,
              shown_on TEXT,
              category TEXT,
              max_points INTEGER
            );
            INSERT INTO seen VALUES (
              'aw-legacy-points',
              'https://zenn.dev/sample/articles/legacy-points',
              'HNでも言及された架空記事',
              '2026-09-12',
              NULL,
              'noise',
              100
            );
            """
        )

    with SeenStore(path) as store:
        assert [
            candidate.id
            for candidate in store.zenn_recheck_candidates(date(2026, 9, 13))
        ] == ["aw-legacy-points"]

        ten_likes = _item(
            "aw-legacy-points",
            likes=10,
            source="zenn-llm",
            url="https://zenn.dev/sample/articles/legacy-points",
        )
        assert store.filter_new(
            [ten_likes],
            date(2026, 9, 13),
            popularity_sources={"zenn-llm"},
        ) == [ten_likes]
