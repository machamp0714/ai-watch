from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from .adapters import ADAPTERS, FetchContext, TimeWindow
from .config import Settings, SourceConfig
from .models import RawItem, raw_to_dict
from .watchlist import source_is_enabled

SAME_HOST_DELAY_S = 2.0


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


def _host_key(cfg: SourceConfig) -> str:
    """同じホストへのリクエストをまとめる鍵。url が無いソースは単独グループにする。"""
    url = cfg.params.get("url", "")
    netloc = urlsplit(url).netloc if url else ""
    return netloc or f"__{cfg.id}"


def _fetch_group(
    cfgs: list[SourceConfig], window: TimeWindow, ctx: FetchContext, same_host_delay_s: float,
) -> list[tuple[SourceConfig, list[RawItem] | Exception]]:
    """同一ホストのソースを直列に、リクエスト間に delay を挟んで取得する（429 対策）。"""
    out: list[tuple[SourceConfig, list[RawItem] | Exception]] = []
    for i, cfg in enumerate(cfgs):
        if i > 0:
            time.sleep(same_host_delay_s)
        try:
            out.append((cfg, _fetch_one(cfg, window, ctx)))
        except Exception as e:  # ソース単位の障害分離（呼び出し側で warnings に積む）
            out.append((cfg, e))
    return out


def collect(
    settings: Settings, window: TimeWindow, ctx: FetchContext, *, day: date,
    types: set[str] | None = None, exclude_types: set[str] = frozenset({"x_mcp"}),
    only_ids: set[str] | None = None, max_workers: int = 8, same_host_delay_s: float = SAME_HOST_DELAY_S,
) -> CollectResult:
    """全ソースを並列取得。同じホスト宛のソースはグループ内で直列化する（レート制限対策）。
    ソース単位で例外を隔離し warnings に積む。raw は window 適用前の全件を保存する。"""
    targets = [
        s for s in settings.sources
        if (types is None or s.type in types) and s.type not in exclude_types
        and (only_ids is None or s.id in only_ids)
        and source_is_enabled(s.id, settings.watchlist)
    ]
    groups: dict[str, list[SourceConfig]] = {}
    for cfg in targets:
        groups.setdefault(_host_key(cfg), []).append(cfg)

    result = CollectResult()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_group, cfgs, window, ctx, same_host_delay_s) for cfgs in groups.values()]
        for fut in futures:
            for cfg, fetched in fut.result():
                if isinstance(fetched, UnknownAdapterError):
                    result.warnings.append(f"{cfg.id}: {fetched}")
                    continue
                if isinstance(fetched, Exception):
                    e = fetched
                    first_line = (str(e).splitlines() or [""])[0][:160]
                    result.warnings.append(f"{cfg.id}: {type(e).__name__}: {first_line}")
                    continue
                save_raw(settings.data_dir, day, cfg.id, fetched)
                kept = [i for i in fetched if i.published_at is None or window.contains(i.published_at)]
                result.items.extend(kept)
                result.counts[cfg.id] = len(kept)
    return result
