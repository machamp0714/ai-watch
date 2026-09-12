from pathlib import Path

import pytest

from ai_watch.watchlist import (
    WatchedTool,
    WatchlistError,
    load_watchlist,
    source_is_enabled,
    watchlist_profile,
    watchlist_rows,
)


def _write(tmp_path: Path, document: str) -> Path:
    path = tmp_path / "watchlist.yaml"
    path.write_text(document, encoding="utf-8")
    return path


def test_load_watchlist_with_pending_tool(tmp_path):
    path = _write(tmp_path, """version: 1
tools:
  - id: example-tool
    name: サンプルツール
    enabled: true
    focus: [サンプルの更新]
    source_ids: []
""")

    tools = load_watchlist(path, set())

    assert tools[0].enabled is True
    assert tools[0].source_ids == ()
    assert tools[0].focus == ("サンプルの更新",)


def test_load_watchlist_accepts_empty_and_disabled_tools(tmp_path):
    assert load_watchlist(_write(tmp_path, "version: 1\ntools: []\n"), set()) == []

    path = _write(tmp_path, """version: 1
tools:
  - id: sample
    name: サンプル
    enabled: false
    focus: []
    source_ids: [feed]
""")
    assert load_watchlist(path, {"feed"})[0].enabled is False


@pytest.mark.parametrize("document", [
    "version: true\ntools: []\n",
    "version: 2\ntools: []\n",
    "version: 1\nversion: 1\ntools: []\n",
    "version: 1\ntools: wrong\n",
    "version: 1\ntools: []\nextra: 1\n",
])
def test_rejects_invalid_root(tmp_path, document):
    with pytest.raises(WatchlistError):
        load_watchlist(_write(tmp_path, document), set())


@pytest.mark.parametrize(("record", "known_source_ids", "field"), [
    ("id: sample\n    name: S\n    enabled: 'false'\n    focus: []\n    source_ids: []", set(), "enabled"),
    ("id: sample\n    name: S\n    enabled: true\n    focus: []\n    source_ids: []\n    extra: x", set(), "フィールド"),
    ("id: sample\n    name: S\n    enabled: true\n    focus: []", set(), "フィールド"),
    ("id: sample\n    name: '   '\n    enabled: true\n    focus: []\n    source_ids: []", set(), "name"),
    ("id: sample\n    name: S\n    enabled: true\n    focus: {x: y}\n    source_ids: []", set(), "focus"),
    ("id: sample\n    name: S\n    enabled: true\n    focus: ['   ']\n    source_ids: []", set(), "focus"),
    ("id: sample\n    name: S\n    enabled: true\n    focus: []\n    source_ids: [feed, feed]", {"feed"}, "source_ids"),
    ("id: sample\n    name: S\n    enabled: true\n    focus: []\n    source_ids: [missing]", set(), "source_ids"),
    ("id: ../secret\n    name: S\n    enabled: true\n    focus: []\n    source_ids: []", set(), "id"),
])
def test_rejects_invalid_tool_record(tmp_path, record, known_source_ids, field):
    path = _write(tmp_path, f"version: 1\ntools:\n  - {record}\n")
    with pytest.raises(WatchlistError, match=field):
        load_watchlist(path, known_source_ids)


def test_rejects_duplicate_tool_id(tmp_path):
    record = """id: sample
    name: S
    enabled: true
    focus: []
    source_ids: []"""
    path = _write(tmp_path, f"version: 1\ntools:\n  - {record}\n  - {record}\n")
    with pytest.raises(WatchlistError, match="id"):
        load_watchlist(path, set())


def test_yaml_error_does_not_include_private_value(tmp_path):
    path = _write(tmp_path, "version: 1\ntools: [private-interest\n")
    with pytest.raises(WatchlistError) as exc_info:
        load_watchlist(path, set())
    message = str(exc_info.value)
    assert "watchlist.yaml" in message and "行" in message
    assert "private-interest" not in message


def test_duplicate_key_error_includes_line_without_document_value(tmp_path):
    path = _write(tmp_path, "version: 1\nversion: private-value\ntools: []\n")
    with pytest.raises(WatchlistError) as exc_info:
        load_watchlist(path, set())
    message = str(exc_info.value)
    assert "2行目" in message and "設定キー" in message
    assert "private-value" not in message


def test_missing_file_uses_filename_without_parent_path(tmp_path):
    path = tmp_path / "private-user-name" / "watchlist.yaml"
    with pytest.raises(WatchlistError) as exc_info:
        load_watchlist(path, set())
    message = str(exc_info.value)
    assert "watchlist.yaml" in message
    assert "private-user-name" not in message


def test_invalid_text_encoding_is_reported_as_watchlist_error(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_bytes(b"\xffprivate-value")
    with pytest.raises(WatchlistError) as exc_info:
        load_watchlist(path, set())
    assert "watchlist.yaml" in str(exc_info.value)
    assert "private-value" not in str(exc_info.value)


def test_shared_and_unowned_sources():
    tools = [
        WatchedTool("a", "A", False, (), ("shared", "a-only")),
        WatchedTool("b", "B", True, (), ("shared",)),
    ]
    assert source_is_enabled("shared", tools)
    assert not source_is_enabled("a-only", tools)
    assert source_is_enabled("community", tools)
    assert source_is_enabled("anything", [])


def test_rows_and_profile():
    tools = [
        WatchedTool("a", "対象A", True, ("架空の更新",), ("feed",)),
        WatchedTool("b", "対象B", True, (), ()),
        WatchedTool("c", "対象C", False, (), ("feed",)),
    ]

    rows = watchlist_rows(tools)
    assert [row["status"] for row in rows] == [
        "有効（取得結果は別途確認）", "設定待ち", "停止中",
    ]
    assert rows[0]["source_ids"] == ["feed"]
    profile = watchlist_profile(tools)
    assert "対象A" in profile and "架空の更新" in profile
    assert "対象B" in profile and "有用な更新" in profile
    assert "対象C" not in profile
    assert watchlist_profile([]) == ""
    assert watchlist_profile([tools[2]]) == ""
