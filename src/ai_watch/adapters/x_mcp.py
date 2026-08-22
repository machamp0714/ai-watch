from __future__ import annotations

import json
from datetime import timezone

from ..config import SourceConfig
from ..models import RawItem
from .base import FetchContext, TimeWindow, excerpt, parse_iso

READ_ONLY_TOOLS = [
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_snapshot",
    "mcp__playwright__browser_press_key",
    "mcp__playwright__browser_wait_for",
    "mcp__playwright__browser_find",
    "mcp__playwright__browser_tabs",
    "mcp__playwright__browser_close",
]


class XMcpAdapter:
    """X のリスト / ブックマークを Playwright MCP 経由の claude -p で読む。閲覧系ツールのみ許可。"""

    def fetch(self, cfg: SourceConfig, window: TimeWindow, ctx: FetchContext) -> list[RawItem]:
        if ctx.runner is None:
            raise RuntimeError("x_mcp requires ctx.runner (ClaudeRunner)")
        template = (ctx.root / cfg.params.get("prompt", "prompts/x_collect.md")).read_text(encoding="utf-8")
        prompt = (
            template.replace("{{URLS}}", "\n".join(f"- {u}" for u in cfg.params["urls"]))
            .replace("{{WINDOW_START}}", window.start.isoformat())
            .replace("{{MAX_POSTS}}", str(cfg.params.get("max_posts", 60)))
        )
        schema = json.loads((ctx.root / "schemas" / "x_items.schema.json").read_text(encoding="utf-8"))
        mcp_config = ctx.root / cfg.params.get("mcp_config", "config/playwright-mcp.json")

        res = ctx.runner.run(
            prompt, schema, budget_usd=float(cfg.params.get("budget_usd", 1.5)),
            mcp_config=mcp_config, allowed_tools=READ_ONLY_TOOLS, effort="medium", retries=0, timeout_s=900,
        )
        if not res.ok:
            raise RuntimeError(f"x-collect failed: {res.error}")
        if not res.data.get("logged_in", False):
            raise RuntimeError(f"x-collect: not logged in ({res.data.get('notes', '')})")

        out: list[RawItem] = []
        for p in res.data.get("posts", []):
            text = (p.get("text") or "").strip()
            links = [l for l in (p.get("links") or []) if l.startswith("http")]
            post_url = p["url"]
            url = links[0] if len(links) == 1 else post_url
            excerpt_parts = [excerpt(text, 400), f"X post: {post_url}"]
            if len(links) > 1:
                excerpt_parts.append("links: " + " ".join(links))
            dt = parse_iso(p.get("posted_at") or None)
            out.append(RawItem(
                source=cfg.id, url=url, title=f"@{p.get('author', '?')}: {text[:80]}".strip(),
                excerpt="\n".join(excerpt_parts)[:600],
                published_at=dt.astimezone(timezone.utc) if dt else None,
                metrics={"likes": int(p.get("likes") or 0), "reposts": int(p.get("reposts") or 0)},
                lang=p.get("lang") or "en",
            ))
        return out
