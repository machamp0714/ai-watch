from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .watchlist import WatchedTool, WatchlistError, load_watchlist

DEFAULT_VAULT_DIR = (
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Personal/00_Self/ai-watch"
)


@dataclass
class SourceConfig:
    id: str
    type: str
    group: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    vault_dir: Path
    data_dir: Path
    window_hours: int
    user_agent: str
    claude_bin: str
    npx_bin: str
    model: str
    sources: list[SourceConfig]
    root: Path  # sources.yaml のあるディレクトリ（prompts/ schemas/ config/ の基準）
    watchlist: list[WatchedTool] = field(default_factory=list)

    def source_groups(self) -> dict[str, str]:
        return {s.id: s.group for s in self.sources}


def _path(value: str | None, default: str, base: Path) -> Path:
    p = Path(os.path.expanduser(value or default))
    return p if p.is_absolute() else (base / p).resolve()


def load_source_ids(path: Path) -> set[str]:
    path = Path(path)
    try:
        document = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WatchlistError(f"収集先設定 {path.name} を読み込めません") from exc
    try:
        raw = yaml.safe_load(document) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f"{mark.line + 1}行目" if mark is not None else "位置不明"
        raise WatchlistError(
            f"収集先設定 {path.name} の{location}にYAML構文エラーがあります"
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("sources", []), list):
        raise WatchlistError("収集先設定のsourcesはリストにしてください")
    ids: list[str] = []
    for index, source in enumerate(raw.get("sources", []), start=1):
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("id"), str)
            or not source["id"].strip()
        ):
            raise WatchlistError(
                f"sources[{index}].idは空白でない文字列にしてください"
            )
        ids.append(source["id"])
    if len(ids) != len(set(ids)):
        raise WatchlistError("収集先IDが重複しています")
    return set(ids)


def load_settings(path: Path) -> Settings:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    d = raw.get("defaults") or {}
    root = path.parent

    sources: list[SourceConfig] = []
    for s in raw.get("sources") or []:
        s = dict(s)
        sources.append(SourceConfig(
            id=s.pop("id"), type=s.pop("type"), group=s.pop("group", "misc"), params=s,
        ))

    watchlist: list[WatchedTool] = []
    watchlist_env = "AI_WATCH_WATCHLIST_FILE"
    has_watchlist = watchlist_env in os.environ or "watchlist_file" in d
    watchlist_file = os.environ[watchlist_env] if watchlist_env in os.environ else d.get("watchlist_file")
    if has_watchlist:
        if not isinstance(watchlist_file, str) or not watchlist_file.strip():
            raise WatchlistError("watchlist_fileは空でないパスにしてください")
        source_ids = [source.id for source in sources]
        if len(set(source_ids)) != len(source_ids):
            raise WatchlistError("収集先IDが重複しています")
        watchlist = load_watchlist(_path(watchlist_file, watchlist_file, root), set(source_ids))

    return Settings(
        vault_dir=_path(os.environ.get("AI_WATCH_VAULT_DIR") or d.get("vault_dir"), DEFAULT_VAULT_DIR, root),
        data_dir=_path(os.environ.get("AI_WATCH_DATA_DIR") or d.get("data_dir"), "./data", root),
        window_hours=int(d.get("window_hours", 30)),
        user_agent=str(d.get("user_agent", "ai-watch/0.1 (personal reader; github.com/machamp0714)")),
        claude_bin=os.path.expanduser(str(d.get("claude_bin", "claude"))),
        npx_bin=os.path.expanduser(str(d.get("npx_bin", "npx"))),
        model=str(d.get("model", "sonnet")),
        sources=sources,
        root=root,
        watchlist=watchlist,
    )
