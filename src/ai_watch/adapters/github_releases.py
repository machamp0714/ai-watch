from __future__ import annotations

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt, parse_iso


class GithubReleasesAdapter:
    """GitHub Releases API。params: url(api.github.com/repos/{o}/{r}/releases?per_page=N), title_prefix。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        resp = ctx.http.get(cfg.params["url"], headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        prefix = cfg.params.get("title_prefix", "")
        out: list[RawItem] = []
        for r in resp.json():
            if r.get("draft"):
                continue
            name = r.get("name") or r.get("tag_name") or ""
            out.append(RawItem(
                source=cfg.id, url=r["html_url"], title=f"{prefix}{name}".strip(),
                excerpt=excerpt(r.get("body") or "", 800), published_at=parse_iso(r.get("published_at")),
                lang="en",
            ))
        return out
