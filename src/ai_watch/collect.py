from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .adapters import ADAPTERS, FetchContext, TimeWindow
from .config import Settings, SourceConfig
from .models import RawItem, raw_to_dict


class UnknownAdapterError(Exception):
    """未知の adapter type が指定された場合の例外。"""
    pass


@dataclass
class CollectResult:
    items: list[RawItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def save_raw(data_dir: Path, day: date, source_id: str, items: list[RawItem]) -> Path:
    path = data_dir / "raw" / day.isoformat() / f"{source_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([raw_to_dict(i) for i in items], ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _fetch_one(cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
    adapter = ADAPTERS.get(cfg.type)
    if adapter is None:
        raise UnknownAdapterError(f"unknown adapter type '{cfg.type}'")
    return adapter.fetch(cfg, window, ctx)


def collect(
    settings: Settings, window: TimeWindow, ctx: FetchContext, *, day: date,
    types: set[str] | None = None, exclude_types: set[str] = frozenset({"x_mcp"}),
    only_ids: set[str] | None = None, max_workers: int = 8,
) -> CollectResult:
    """全ソースを並列取得。ソース単位で例外を隔離し warnings に積む。raw は window 適用前の全件を保存する。"""
    targets = [
        s for s in settings.sources
        if (types is None or s.type in types) and s.type not in exclude_types
        and (only_ids is None or s.id in only_ids)
    ]
    result = CollectResult()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, cfg, window, ctx): cfg for cfg in targets}
        for fut, cfg in futures.items():
            try:
                fetched = fut.result()
                save_raw(settings.data_dir, day, cfg.id, fetched)
                kept = [i for i in fetched if i.published_at is None or window.contains(i.published_at)]
                result.items.extend(kept)
                result.counts[cfg.id] = len(kept)
            except UnknownAdapterError as e:
                result.warnings.append(f"{cfg.id}: {e}")
                continue
            except Exception as e:  # ソース単位の障害分離
                result.warnings.append(f"{cfg.id}: {type(e).__name__}: {e}"[:200])
                continue
    return result
