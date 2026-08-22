from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


class HtmlDiffAdapter:
    """RSS の無いページ向け。<a href> のうち link_pattern に合う絶対 URL の集合を前回と比較し、増えた分を返す。
    初回は状態を記録するだけで 0 件（過去記事で溢れさせない）。本文はパースしない。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        page_url = cfg.params["url"]
        resp = ctx.http.get(page_url)
        resp.raise_for_status()
        parser = _LinkCollector()
        parser.feed(resp.text)
        pattern = re.compile(cfg.params.get("link_pattern", "."))

        links: dict[str, str] = {}
        for href, text in parser.links:
            absolute = urljoin(page_url, href).split("#")[0]
            if pattern.search(absolute) and absolute not in links:
                links[absolute] = text

        state_path = ctx.data_dir / "state" / "html_diff" / f"{cfg.id}.json"
        known: set[str] | None = set(json.loads(state_path.read_text())) if state_path.exists() else None
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(sorted(links), ensure_ascii=False, indent=0))
        if known is None:
            return []

        now = datetime.now(timezone.utc)
        return [
            RawItem(source=cfg.id, url=u, title=links[u] or u, excerpt="", published_at=now,
                    lang=cfg.params.get("lang", "en"))
            for u in links if u not in known
        ]
