from __future__ import annotations

import re

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt

_SLUG_DROP = re.compile(r"[^a-z0-9.-]")


def _slug(group: str) -> str:
    """見出しの group(1) から URL フラグメントを作る: 小文字化 → 空白は '-' → [a-z0-9.-] 以外は削除。
    例: '2.1.239' -> '2.1.239'（変化なし）, 'August 20, 2026' -> 'august-20-2026'。"""
    return _SLUG_DROP.sub("", group.lower().replace(" ", "-"))


class GithubFileSectionsAdapter:
    """Markdown ファイル（CHANGELOG.md 等）を見出しで分割し、先頭 N セクションを返す。
    日付が無いので published_at=None。新着判定は url#version を id にした seen に委ねる。
    params: url(raw), page_url(人が開く URL), section_pattern(group(1)=version), title_prefix, max_sections。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        resp = ctx.http.get(cfg.params["url"])
        resp.raise_for_status()
        text = resp.text
        pattern = re.compile(cfg.params["section_pattern"], re.M)
        matches = list(pattern.finditer(text))
        page_url = cfg.params.get("page_url", cfg.params["url"])
        prefix = cfg.params.get("title_prefix", "")
        limit = int(cfg.params.get("max_sections", 5))

        out: list[RawItem] = []
        for i, m in enumerate(matches[:limit]):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[m.end():end].strip()
            version = m.group(1)
            out.append(RawItem(
                source=cfg.id, url=f"{page_url}#{_slug(version)}", title=f"{prefix}{version}".strip(),
                excerpt=excerpt(body, 2000), published_at=None, lang="en",
            ))
        return out
