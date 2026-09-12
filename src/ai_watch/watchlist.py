from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class WatchedTool:
    id: str
    name: str
    enabled: bool
    focus: tuple[str, ...]
    source_ids: tuple[str, ...]


class WatchlistError(ValueError):
    """監視リストを安全に読み込めない場合の設定エラー。"""


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False,
) -> dict[str, Any]:
    loader.flatten_mapping(node)
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            line = key_node.start_mark.line + 1
            raise WatchlistError(f"{line}行目の設定キーは重複しない文字列にしてください")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)

_TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ROOT_KEYS = {"version", "tools"}
_TOOL_KEYS = {"id", "name", "enabled", "focus", "source_ids"}


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object, *, record: int, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not _nonempty_string(item) for item in value):
        raise WatchlistError(f"tools[{record}].{field}は空白でない文字列のリストにしてください")
    return tuple(value)


def _parse_tool(
    value: object, *, record: int, known_source_ids: set[str], seen_ids: set[str],
) -> WatchedTool:
    if not isinstance(value, dict) or set(value) != _TOOL_KEYS:
        raise WatchlistError(f"tools[{record}]のフィールドを必須項目だけにしてください")

    tool_id = value["id"]
    if not isinstance(tool_id, str) or _TOOL_ID.fullmatch(tool_id) is None:
        raise WatchlistError(f"tools[{record}].idの形式が不正です")
    if tool_id in seen_ids:
        raise WatchlistError(f"tools[{record}].idが重複しています")

    name = value["name"]
    if not _nonempty_string(name):
        raise WatchlistError(f"tools[{record}].nameは空白でない文字列にしてください")

    enabled = value["enabled"]
    if type(enabled) is not bool:
        raise WatchlistError(f"tools[{record}].enabledは真偽値にしてください")

    focus = _string_list(value["focus"], record=record, field="focus")
    source_ids = _string_list(value["source_ids"], record=record, field="source_ids")
    if len(source_ids) != len(set(source_ids)):
        raise WatchlistError(f"tools[{record}].source_idsに重複があります")
    if any(source_id not in known_source_ids for source_id in source_ids):
        raise WatchlistError(f"tools[{record}].source_idsに存在しない収集先があります")

    seen_ids.add(tool_id)
    return WatchedTool(tool_id, name, enabled, focus, source_ids)


def load_watchlist(path: Path, known_source_ids: set[str]) -> list[WatchedTool]:
    path = Path(path)
    try:
        document = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WatchlistError(f"監視リスト {path.name} を読み込めません") from exc

    try:
        raw = yaml.load(document, Loader=UniqueKeyLoader)
    except WatchlistError:
        raise
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"{mark.line + 1}行目" if mark is not None else "位置不明"
        raise WatchlistError(f"監視リスト {path.name} の{location}にYAML構文エラーがあります") from exc

    if not isinstance(raw, dict) or set(raw) != _ROOT_KEYS:
        raise WatchlistError("監視リストのルートのフィールドはversionとtoolsだけにしてください")
    version = raw["version"]
    if type(version) is not int or version != 1:
        raise WatchlistError("監視リストのversionは整数の1にしてください")
    values = raw["tools"]
    if not isinstance(values, list):
        raise WatchlistError("監視リストのtoolsはリストにしてください")

    seen_ids: set[str] = set()
    return [
        _parse_tool(value, record=index, known_source_ids=known_source_ids, seen_ids=seen_ids)
        for index, value in enumerate(values, start=1)
    ]


def source_is_enabled(source_id: str, tools: list[WatchedTool]) -> bool:
    owners = [tool for tool in tools if source_id in tool.source_ids]
    return not owners or any(tool.enabled for tool in owners)


def watchlist_rows(tools: list[WatchedTool]) -> list[dict[str, object]]:
    return [
        {
            "id": tool.id,
            "name": tool.name,
            "status": (
                "停止中" if not tool.enabled else
                "設定待ち" if not tool.source_ids else
                "有効（取得結果は別途確認）"
            ),
            "source_ids": list(tool.source_ids),
        }
        for tool in tools
    ]


def watchlist_profile(tools: list[WatchedTool]) -> str:
    active = [tool for tool in tools if tool.enabled]
    if not active:
        return ""
    lines = [
        "## 利用中ツールの関心情報",
        "一致だけで強制掲載せず、未知の発見も残してください。",
        "入力、得られる結果、現在の作業との差を具体的に説明してください。",
    ]
    lines.extend(
        f"- {tool.name}：{'、'.join(tool.focus) or '有用な更新'}"
        for tool in active
    )
    return "\n".join(lines)
