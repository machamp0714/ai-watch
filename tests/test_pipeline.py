import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from ai_watch.adapters import ADAPTERS
from ai_watch.claude_runner import ClaudeResult
from ai_watch.config import Settings, SourceConfig
from ai_watch.models import RawItem
from ai_watch.pipeline import STAGES, run_nightly, today_jst
from ai_watch.seen import SeenStore
from ai_watch.vault import Vault
from ai_watch.watchlist import WatchedTool

REPO_ROOT = Path(__file__).resolve().parents[1]   # prompts/ schemas/ は本物を使う
RSS = b"""<rss version="2.0"><channel><title>t</title>
<item><title>Claude Code hooks</title><link>https://ex.com/hooks</link><pubDate>Sat, 22 Aug 2026 10:00:00 GMT</pubDate></item>
<item><title>Codex cloud</title><link>https://ex.com/cloud</link><pubDate>Sat, 22 Aug 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""
NOW = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)   # JST 8/23 05:00
DAY = date(2026, 8, 23)


class FakeRunner:
    """プロンプト中の id を拾い、最初を try、残りを read にする。"""
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def run(self, prompt, schema, **kw):
        self.calls += 1
        if self.fail:
            return ClaudeResult(False, None, 0.3, "error_max_budget_usd", error="budget")
        ids = list(dict.fromkeys(re.findall(r"aw-[0-9a-f]{8}", prompt)))
        items = [{"id": i, "category": "try" if n == 0 else "read", "score": 90 - n,
                  "signals": {"attention": 1, "tryability": 2, "jp_gap": 1, "relevance": 2},
                  "reason": "テスト", "try_plan": "やる", "article_angle": "角度"} for n, i in enumerate(ids)]
        return ClaudeResult(True, {"items": items}, 0.11, "success")


class CaptureRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def run(self, prompt, schema, **kwargs):
        self.prompts.append(prompt)
        return super().run(prompt, schema, **kwargs)


def _settings(tmp_path: Path) -> Settings:
    return Settings(vault_dir=tmp_path / "vault", data_dir=tmp_path / "data", window_hours=30, user_agent="t",
                    claude_bin="claude", npx_bin="npx", model="sonnet", root=REPO_ROOT,
                    sources=[SourceConfig(id="feed", type="rss", group="en", params={"url": "https://ok/feed"}),
                             SourceConfig(id="bad", type="rss", group="en", params={"url": "https://bad/feed"})])


def _http():
    return httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=RSS) if req.url.host == "ok" else httpx.Response(403)))


def test_today_jst():
    assert today_jst(NOW) == DAY


def test_nightly_end_to_end(tmp_path):
    s = _settings(tmp_path)
    v = Vault(s.vault_dir)
    v.write_atomic(v.backlog, "# Backlog\n\n## 候補\n")
    v.write_atomic(v.profile, "興味: Claude Code")
    notes = []
    runner = FakeRunner()
    r = run_nightly(s, DAY, now=NOW, runner=runner, http=_http(), notifier=lambda t, m: notes.append(m))

    assert r.collected == 2 and r.new == 2 and r.mode == "triaged" and r.n_try == 1 and r.cost_usd == 0.11
    assert r.warnings and r.warnings[0].startswith("bad:")
    md = v.read_digest(DAY)
    assert md and "- [ ] 🧪 **Claude Code hooks**" in md and "  - 試し方: やる" in md
    digest_json = json.loads(v.digest_path(DAY).with_suffix(".json").read_text(encoding="utf-8"))
    assert digest_json["date"] == DAY.isoformat()
    assert digest_json["items_shown"] == r.shown
    assert "> ⚠ 取得失敗: bad:" in md
    assert (s.data_dir / "work" / "2026-08-23" / "triage.json").exists()
    assert (s.data_dir / "raw" / "2026-08-23" / "feed.json").exists()
    assert v.log.read_text().strip().endswith("/ 失敗: bad")
    assert notes == ["2026-08-23: mode=triaged / 失敗 1 件"]
    with SeenStore(s.data_dir / "seen.sqlite") as seen:
        assert seen.get(r.try_ids[0])["shown_on"] == "2026-08-23"

    # 翌日: 同じフィードでも新着ゼロ、runner は呼ばれない
    r2 = run_nightly(s, date(2026, 8, 24), now=NOW, runner=runner, http=_http(), notifier=lambda t, m: None)
    assert r2.new == 0 and runner.calls == 1
    assert "新着はありませんでした" in v.read_digest(date(2026, 8, 24))


def test_nightly_rechecks_unshown_source_after_likes_cross_a_tier(tmp_path, monkeypatch):
    class GrowingAdapter:
        likes = 2

        def fetch(self, cfg, window, ctx):
            return [RawItem(
                source=cfg.id,
                url="https://zenn.dev/sample/articles/growing",
                title="後から注目された架空記事",
                published_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                metrics={"likes": self.likes},
                lang="ja",
            )]

    class NoiseThenReadRunner:
        calls = 0

        def run(self, prompt, schema, **kwargs):
            self.calls += 1
            item_id = re.search(r"aw-[0-9a-f]{8}", prompt).group(0)
            items = [] if self.calls == 1 else [{
                "id": item_id,
                "category": "read",
                "score": 60,
                "signals": {
                    "attention": 2,
                    "tryability": 0,
                    "jp_gap": 1,
                    "relevance": 2,
                },
                "reason": "人気度が伸びた",
                "article_angle": "後から注目された理由",
                "summary": "架空の要約",
            }]
            return ClaudeResult(True, {"items": items}, 0.01, "success")

    adapter = GrowingAdapter()
    monkeypatch.setitem(ADAPTERS, "growing_zenn", adapter)
    settings = _settings(tmp_path)
    settings.sources = [SourceConfig(
        id="zenn",
        type="growing_zenn",
        group="jp",
        params={"recheck_popularity": True},
    )]
    runner = NoiseThenReadRunner()

    first = run_nightly(
        settings,
        DAY,
        now=NOW,
        runner=runner,
        http=_http(),
        notifier=lambda *args: None,
    )
    adapter.likes = 10
    second_day = date(2026, 8, 24)
    second = run_nightly(
        settings,
        second_day,
        now=NOW,
        runner=runner,
        http=_http(),
        notifier=lambda *args: None,
    )

    assert first.new == 1 and first.shown == 0
    assert second.new == 1 and second.shown == 1
    assert runner.calls == 2


def test_nightly_rechecks_zenn_article_after_it_leaves_the_lists(tmp_path):
    state = {"day": 1}
    detail_calls = []

    def article(likes):
        return {
            "id": 1,
            "post_type": "Article",
            "title": "後から注目された架空記事",
            "slug": "growing-later",
            "comments_count": 1,
            "liked_count": likes,
            "bookmarked_count": 3,
            "body_letters_count": 1200,
            "article_type": "tech",
            "emoji": "🧪",
            "is_suspending": False,
            "published_at": "2026-08-22T10:00:00+09:00",
            "body_updated_at": "2026-08-22T10:00:00+09:00",
            "source_repo_updated_at": None,
            "pinned": False,
            "path": "/sample/articles/growing-later",
            "principal_type": "User",
            "user": {"id": 1, "username": "sample", "name": "架空ユーザー"},
            "publication": None,
        }

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles/growing-later":
            detail_calls.append(request)
            value = article(10)
            value["body_html"] = "<p>後から評価が増えました。</p>"
            return httpx.Response(200, json={"article": value})
        if request.url.path == "/api/articles":
            values = [article(2)] if state["day"] == 1 else []
            return httpx.Response(200, json={"articles": values, "next_page": None})
        if request.url.path == "/topics/llm/feed":
            rss_item = "" if state["day"] == 1 else """
                <item><title>後から注目された架空記事</title>
                <link>https://zenn.dev/sample/articles/growing-later</link>
                <pubDate>Sat, 22 Aug 2026 01:00:00 GMT</pubDate></item>
            """
            return httpx.Response(
                200,
                content=(
                    f'<rss version="2.0"><channel><title>Zenn</title>{rss_item}'
                    "</channel></rss>"
                ).encode(),
            )
        return httpx.Response(404)

    class NoiseThenReadRunner:
        calls = 0

        def run(self, prompt, schema, **kwargs):
            self.calls += 1
            item_id = re.search(r"aw-[0-9a-f]{8}", prompt).group(0)
            items = [] if self.calls == 1 else [{
                "id": item_id,
                "category": "read",
                "score": 60,
                "signals": {
                    "attention": 2,
                    "tryability": 0,
                    "jp_gap": 1,
                    "relevance": 2,
                },
                "reason": "人気度が伸びた",
                "article_angle": "後から注目された理由",
                "summary": "架空の要約",
            }]
            return ClaudeResult(True, {"items": items}, 0.01, "success")

    settings = _settings(tmp_path)
    settings.sources = [SourceConfig(
        id="zenn-llm",
        type="zenn",
        group="jp",
        params={
            "topic": "llm",
            "url": "https://zenn.dev/topics/llm/feed",
            "lang": "ja",
            "recheck_popularity": True,
        },
    )]
    runner = NoiseThenReadRunner()
    http = httpx.Client(transport=httpx.MockTransport(responder))

    first = run_nightly(
        settings,
        DAY,
        now=NOW,
        runner=runner,
        http=http,
        notifier=lambda *args: None,
    )
    state["day"] = 2
    second = run_nightly(
        settings,
        date(2026, 8, 24),
        now=NOW,
        runner=runner,
        http=http,
        notifier=lambda *args: None,
    )

    assert first.new == 1 and first.shown == 0
    assert second.new == 1 and second.shown == 1
    assert len(detail_calls) == 1
    assert runner.calls == 2


def test_nightly_can_skip_x_collect_and_saves_empty_stage(tmp_path):
    settings = _settings(tmp_path)
    settings.sources.append(
        SourceConfig(
            id="x",
            type="x_mcp",
            group="x",
            params={"urls": ["https://x.example.invalid/bookmarks"]},
        )
    )

    report = run_nightly(
        settings,
        DAY,
        now=NOW,
        dry_run=True,
        skip_x_collect=True,
        runner=FakeRunner(),
        http=_http(),
        notifier=lambda *args: None,
    )

    stage = json.loads(
        (settings.data_dir / "work" / DAY.isoformat() / "x_collect.json").read_text(
            encoding="utf-8"
        )
    )
    assert stage == {"items": [], "warnings": [], "counts": {}}
    assert report.collected == 2


def test_rerun_from_render_uses_work_files(tmp_path):
    s = _settings(tmp_path)
    runner = FakeRunner()
    run_nightly(s, DAY, now=NOW, runner=runner, http=_http(), notifier=lambda t, m: None)
    first = Vault(s.vault_dir).read_digest(DAY)
    r = run_nightly(s, DAY, from_stage="render", now=NOW, runner=runner, http=None, notifier=lambda t, m: None)
    assert runner.calls == 1 and r.new == 2
    assert Vault(s.vault_dir).read_digest(DAY) == first


def test_dry_run_touches_nothing_in_vault(tmp_path):
    s = _settings(tmp_path)
    r = run_nightly(s, DAY, dry_run=True, now=NOW, runner=FakeRunner(), http=_http(), notifier=lambda t, m: None)
    assert not (s.vault_dir / "digests").exists() and not (s.vault_dir / "log.md").exists()
    assert Path(r.digest_path) == s.data_dir / "work" / "2026-08-23" / "digest.md" and Path(r.digest_path).exists()
    assert Path(r.digest_path).with_suffix(".json").exists()
    assert not (s.data_dir / "seen.sqlite").exists()


def test_dry_run_does_not_migrate_an_existing_seen_database(tmp_path):
    settings = _settings(tmp_path)
    settings.sources = [SourceConfig(
        id="zenn-llm",
        type="zenn",
        group="jp",
        params={
            "topic": "llm",
            "url": "https://zenn.dev/topics/llm/feed",
            "lang": "ja",
            "recheck_popularity": True,
        },
    )]
    settings.data_dir.mkdir(parents=True)
    seen_path = settings.data_dir / "seen.sqlite"
    with sqlite3.connect(seen_path) as conn:
        conn.executescript(
            """
            CREATE TABLE seen (
              id TEXT PRIMARY KEY,
              url TEXT NOT NULL,
              title TEXT,
              first_seen TEXT NOT NULL,
              shown_on TEXT,
              category TEXT,
              max_points INTEGER
            );
            """
        )
    before = seen_path.read_bytes()

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/articles":
            return httpx.Response(200, json={"articles": [], "next_page": None})
        if request.url.path == "/topics/llm/feed":
            return httpx.Response(
                200,
                content=b'<rss version="2.0"><channel><title>Zenn</title></channel></rss>',
            )
        return httpx.Response(404)

    run_nightly(
        settings,
        DAY,
        dry_run=True,
        now=NOW,
        runner=FakeRunner(),
        http=httpx.Client(transport=httpx.MockTransport(responder)),
        notifier=lambda *args: None,
    )

    with sqlite3.connect(seen_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(seen)")}
    assert {"popularity_source", "max_likes"}.isdisjoint(columns)
    assert seen_path.read_bytes() == before


def test_untriaged_fallback_notifies(tmp_path):
    s = _settings(tmp_path)
    notes = []
    r = run_nightly(s, DAY, now=NOW, runner=FakeRunner(fail=True), http=_http(), notifier=lambda t, m: notes.append(m))
    assert r.mode == "untriaged" and "未トリアージ" in Vault(s.vault_dir).read_digest(DAY)
    assert notes == ["2026-08-23: mode=untriaged / 失敗 1 件"]


def test_sync_decisions_runs_before_triage(tmp_path):
    s = _settings(tmp_path)
    v = Vault(s.vault_dir)
    v.write_atomic(v.backlog, "# Backlog\n\n## 候補\n")
    v.write_atomic(v.digest_path(date(2026, 8, 22)),
                   "- [x] 🧪 **前日の候補** — r ([hn](https://ex.com/prev)) ^aw-00000001\n")
    r = run_nightly(s, DAY, now=NOW, runner=FakeRunner(), http=_http(), notifier=lambda t, m: None)
    assert r.decisions_added == 1
    assert "^aw-00000001" in v.backlog.read_text()
    assert json.loads((s.data_dir / "decisions.jsonl").read_text().splitlines()[0])["decision"] == "try"


def test_worker_checks_are_applied_before_decision_sync(tmp_path):
    settings = _settings(tmp_path)
    settings.checks_dir = tmp_path / "checks"
    vault = Vault(settings.vault_dir)
    vault.write_atomic(vault.backlog, "# Backlog\n\n## 候補\n")
    previous = date(2026, 8, 22)
    vault.write_atomic(
        vault.digest_path(previous),
        "- [ ] 🧪 **架空候補** — 理由 ([sample](https://example.com/item)) ^aw-00000001\n",
    )
    settings.checks_dir.mkdir(parents=True)
    (settings.checks_dir / f"{previous.isoformat()}.jsonl").write_text(
        json.dumps(
            {
                "id": "aw-00000001",
                "category": "try",
                "checked": True,
                "timestamp": "2026-08-22T22:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    run_nightly(
        settings,
        DAY,
        now=NOW,
        runner=FakeRunner(),
        http=_http(),
        notifier=lambda *args: None,
    )

    assert "^aw-00000001" in vault.backlog.read_text(encoding="utf-8")
    decision = json.loads(
        (settings.data_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert decision["id"] == "aw-00000001"
    assert decision["decision"] == "try"


def test_rerun_preserves_checked_boxes(tmp_path):
    s = _settings(tmp_path)
    v = Vault(s.vault_dir)
    v.write_atomic(v.backlog, "# Backlog\n\n## 候補\n")
    v.write_atomic(v.profile, "興味: Claude Code")
    runner = FakeRunner()
    run_nightly(s, DAY, now=NOW, runner=runner, http=_http(), notifier=lambda t, m: None)

    md = v.read_digest(DAY)
    lines = md.splitlines()
    checked_line = None
    for i, line in enumerate(lines):
        if line.startswith("- [ ] 🧪"):
            lines[i] = line.replace("- [ ] 🧪", "- [x] 🧪", 1)
            checked_line = lines[i]
            break
    assert checked_line is not None
    checked_id = re.search(r"\^(aw-[0-9a-f]{8})", checked_line).group(1)
    v.write_atomic(v.digest_path(DAY), "\n".join(lines) + "\n")

    r = run_nightly(s, DAY, from_stage="triage", now=NOW, runner=runner, http=None, notifier=lambda t, m: None)

    store_lines = (s.data_dir / "decisions.jsonl").read_text().splitlines()
    decisions = [json.loads(l) for l in store_lines]
    assert any(d["id"] == checked_id and d["decision"] == "try" for d in decisions)
    assert f"^{checked_id}" in v.backlog.read_text()

    new_md = v.read_digest(DAY)
    new_line = next(l for l in new_md.splitlines() if f"^{checked_id}" in l)
    assert new_line.startswith("- [x] 🧪")
    assert r.decisions_added >= 1

    # 2 回目の再実行: 既に記録済み（filter_new は空）でも、既存ダイジェストにチェックが
    # 残っている限り carry-over 対象の id は checked_ids（found 由来）から見つかるはず。
    r2 = run_nightly(s, DAY, from_stage="triage", now=NOW, runner=runner, http=None, notifier=lambda t, m: None)
    again_md = v.read_digest(DAY)
    again_line = next(l for l in again_md.splitlines() if f"^{checked_id}" in l)
    assert again_line.startswith("- [x] 🧪")
    assert r2.decisions_added == 0        # 2 回目は新規決定なし


def test_rerun_from_render_preserves_checked_boxes(tmp_path):
    s = _settings(tmp_path)
    v = Vault(s.vault_dir)
    v.write_atomic(v.backlog, "# Backlog\n\n## 候補\n")
    v.write_atomic(v.profile, "興味: Claude Code")
    runner = FakeRunner()
    run_nightly(s, DAY, now=NOW, runner=runner, http=_http(), notifier=lambda t, m: None)

    md = v.read_digest(DAY)
    lines = md.splitlines()
    checked_line = None
    for i, line in enumerate(lines):
        if line.startswith("- [ ] 🧪"):
            lines[i] = line.replace("- [ ] 🧪", "- [x] 🧪", 1)
            checked_line = lines[i]
            break
    assert checked_line is not None
    checked_id = re.search(r"\^(aw-[0-9a-f]{8})", checked_line).group(1)
    v.write_atomic(v.digest_path(DAY), "\n".join(lines) + "\n")

    # --from render は triage をやり直さない（work file のキャッシュを使う）が、
    # それでもチェックは引き継がれるべき。
    r = run_nightly(s, DAY, from_stage="render", now=NOW, runner=runner, http=None, notifier=lambda t, m: None)

    assert f"^{checked_id}" in v.backlog.read_text()
    new_md = v.read_digest(DAY)
    new_line = next(l for l in new_md.splitlines() if f"^{checked_id}" in l)
    assert new_line.startswith("- [x] 🧪")
    assert r.decisions_added >= 1


def test_rerun_from_stage_without_work_files_raises_and_keeps_digest(tmp_path):
    s = _settings(tmp_path)
    v = Vault(s.vault_dir)
    v.write_atomic(v.digest_path(DAY), "KEEP")
    with pytest.raises(FileNotFoundError):
        run_nightly(s, DAY, from_stage="render", now=NOW, runner=FakeRunner(), http=None, notifier=lambda t, m: None)
    assert v.read_digest(DAY) == "KEEP"
    assert "FAILED" in v.log.read_text()


def test_render_failure_notifies_and_logs(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    v = Vault(s.vault_dir)
    v.write_atomic(v.backlog, "# Backlog\n\n## 候補\n")
    v.write_atomic(v.profile, "興味: Claude Code")
    notes = []

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("ai_watch.pipeline.render_digest", _boom)
    with pytest.raises(RuntimeError):
        run_nightly(s, DAY, now=NOW, runner=FakeRunner(), http=_http(), notifier=lambda t, m: notes.append(m))
    assert notes == ["2026-08-23: FAILED RuntimeError: boom"]
    assert v.read_digest(DAY) is None
    assert "FAILED: RuntimeError: boom" in v.log.read_text().rstrip().splitlines()[-1]


def test_watchlist_augments_prompt_without_editing_profile(tmp_path):
    settings = _settings(tmp_path)
    settings.watchlist = [
        WatchedTool("example-tool", "サンプルツール", True, ("サンプルの更新",), ()),
    ]
    vault = Vault(settings.vault_dir)
    vault.write_atomic(vault.profile, "元の関心\n")
    runner = CaptureRunner()

    run_nightly(
        settings, DAY, now=NOW, dry_run=True, runner=runner,
        http=_http(), notifier=lambda *args: None,
    )

    assert "サンプルツール" in runner.prompts[0]
    assert "サンプルの更新" in runner.prompts[0]
    assert vault.profile.read_text(encoding="utf-8") == "元の関心\n"


def test_rerun_from_triage_uses_new_watchlist_but_render_does_not_retriage(tmp_path):
    settings = _settings(tmp_path)
    runner = CaptureRunner()
    run_nightly(
        settings, DAY, now=NOW, dry_run=True, runner=runner,
        http=_http(), notifier=lambda *args: None,
    )
    settings.watchlist = [
        WatchedTool("new-tool", "新しい関心", True, ("新しい更新",), ()),
    ]

    run_nightly(
        settings, DAY, from_stage="triage", now=NOW, dry_run=True,
        runner=runner, http=None, notifier=lambda *args: None,
    )
    assert len(runner.prompts) == 2
    assert "新しい関心" in runner.prompts[1]

    run_nightly(
        settings, DAY, from_stage="render", now=NOW, dry_run=True,
        runner=runner, http=None, notifier=lambda *args: None,
    )
    assert len(runner.prompts) == 2


def test_retriage_rejudges_saved_items_without_touching_seen(tmp_path):
    s = _settings(tmp_path)
    runner = FakeRunner()
    run_nightly(s, DAY, now=NOW, runner=runner, http=_http(), notifier=lambda t, m: None)
    seen_db = s.data_dir / "seen.sqlite"
    with sqlite3.connect(seen_db) as con:
        before = sorted(con.execute("SELECT * FROM seen").fetchall())
    first_triage = json.loads((s.data_dir / "work" / DAY.isoformat() / "triage.json").read_text())

    r = run_nightly(s, DAY, from_stage="triage", retriage=True, now=NOW, runner=runner, http=None,
                    notifier=lambda t, m: None)
    assert runner.calls == 2 and r.new == 2
    with sqlite3.connect(seen_db) as con:
        assert sorted(con.execute("SELECT * FROM seen").fetchall()) == before
    backup = s.data_dir / "work" / DAY.isoformat() / "triage.before-retriage.json"
    assert json.loads(backup.read_text()) == first_triage

    run_nightly(s, DAY, from_stage="triage", retriage=True, now=NOW, runner=runner, http=None,
                notifier=lambda t, m: None)
    assert json.loads(backup.read_text()) == first_triage          # 2 回目でも最初の結果を残す


def test_retriage_requires_triage_stage(tmp_path):
    with pytest.raises(ValueError):
        run_nightly(_settings(tmp_path), DAY, from_stage="render", retriage=True, now=NOW,
                    runner=FakeRunner(), http=None, notifier=lambda t, m: None)
