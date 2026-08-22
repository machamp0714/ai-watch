from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow

_EPOCH_ISO = datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()


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
    本文はパースしない。

    状態ファイルは {url: 初出時刻(ISO)}。dry-run や `collect --only` で何度取得しても状態を消費しないよう、
    各 URL の「初めて見つかった時刻」を保持し、window.start 以降に初出したリンクだけを返す
    （既に見えているリンクは、再取得しても初出時刻を更新しない）。
    初回は現在のリンク集合を epoch(1970-01-01) として記録し 0 件を返す（過去記事で溢れさせない）。
    旧形式（URL の list）の状態ファイルは epoch 初出として読み替える。"""

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

        if not links:
            raise RuntimeError(f"no links matched link_pattern on {page_url}")

        state_path = ctx.data_dir / "state" / "html_diff" / f"{cfg.id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        raw_state = json.loads(state_path.read_text()) if state_path.exists() else None

        if raw_state is None:
            state: dict[str, str] = {u: _EPOCH_ISO for u in links}
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=0, sort_keys=True))
            return []

        state = {u: _EPOCH_ISO for u in raw_state} if isinstance(raw_state, list) else dict(raw_state)
        now_iso = datetime.now(timezone.utc).isoformat()
        for u in links:
            if u not in state:
                state[u] = now_iso
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=0, sort_keys=True))

        out: list[RawItem] = []
        for u, text in links.items():
            first_seen_iso = state.get(u)
            if first_seen_iso is None:
                continue
            first_seen = datetime.fromisoformat(first_seen_iso)
            if first_seen >= window.start:
                # window.end は呼び出し元（run_nightly）の冒頭で固定されるが、first_seen=now() は
                # この fetch 実行時刻になるため window.end より後になり得る。window.contains() で
                # 弾かれて発見当日に出ないことがないよう、返す published_at は window.end に丸める
                # （状態ファイルに保存する first_seen 自体は丸めない）。
                published_at = min(first_seen, window.end)
                out.append(RawItem(source=cfg.id, url=u, title=text or u, excerpt="",
                                   published_at=published_at, lang=cfg.params.get("lang", "en")))
        return out
