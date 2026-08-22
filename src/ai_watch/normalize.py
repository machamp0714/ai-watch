from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Item, RawItem

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"fbclid", "gclid", "ref", "ref_src", "ref_url"}
_X_HOST_KEYS = {"s", "t"}  # x.com の共有 URL に付く追跡パラメータ


def _is_tracking(key: str, host: str) -> bool:
    k = key.lower()
    if k.startswith(_TRACKING_PREFIXES) or k in _TRACKING_KEYS:
        return True
    return host == "x.com" and k in _X_HOST_KEYS


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in ("twitter.com", "mobile.twitter.com"):
        host = "x.com"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _is_tracking(k, host)]
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(query), p.fragment))


def item_id(url: str) -> str:
    return "aw-" + hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:8]


_WORD = re.compile(r"[0-9A-Za-z぀-ヿ一-鿿]{2,}")


def _title_tokens(title: str) -> set[str]:
    # " - Zenn" のようなサイト名サフィックスを落としてから分かち書き（雑で良い：2 文字以上の連続）
    t = re.split(r"\s+[-|–—]\s+", title)[0].lower()
    return set(_WORD.findall(t))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _merge(into: Item, r: RawItem) -> None:
    if r.source not in into.mentions:
        into.mentions.append(r.source)
    for k, v in r.metrics.items():
        into.metrics[k] = max(into.metrics.get(k, 0), v)
    if len(r.excerpt) > len(into.excerpt):
        into.excerpt = r.excerpt[:600]
    if into.published_at is None:
        into.published_at = r.published_at


def normalize(raws: list[RawItem], groups: dict[str, str]) -> list[Item]:
    """URL 正規化 → 同一 URL を束ね → タイトル類似（Jaccard ≥ 0.8）を束ねる。順序は入力順を保つ。"""
    by_id: dict[str, Item] = {}
    for r in raws:
        cu = canonical_url(r.url)
        iid = item_id(cu)
        if iid in by_id:
            _merge(by_id[iid], r)
            continue
        by_id[iid] = Item(
            id=iid, url=cu, title=r.title.strip(), excerpt=r.excerpt[:600],
            published_at=r.published_at, metrics=dict(r.metrics), lang=r.lang,
            group=groups.get(r.source, "misc"), source=r.source, mentions=[r.source],
        )

    items = list(by_id.values())
    tokens = {it.id: _title_tokens(it.title) for it in items}
    result: list[Item] = []
    for it in items:
        target = next((kept for kept in result if _jaccard(tokens[kept.id], tokens[it.id]) >= 0.8), None)
        if target is None:
            result.append(it)
        else:
            _merge(target, RawItem(source=it.source, url=it.url, title=it.title, excerpt=it.excerpt,
                                   published_at=it.published_at, metrics=it.metrics, lang=it.lang))
            for m in it.mentions:
                if m not in target.mentions:
                    target.mentions.append(m)
    return result
