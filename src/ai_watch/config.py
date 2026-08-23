from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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

    def source_groups(self) -> dict[str, str]:
        return {s.id: s.group for s in self.sources}


def _path(value: str | None, default: str, base: Path) -> Path:
    p = Path(os.path.expanduser(value or default))
    return p if p.is_absolute() else (base / p).resolve()


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
    )
