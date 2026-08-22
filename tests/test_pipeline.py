import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from ai_watch.claude_runner import ClaudeResult
from ai_watch.config import Settings, SourceConfig
from ai_watch.pipeline import STAGES, run_nightly, today_jst
from ai_watch.seen import SeenStore
from ai_watch.vault import Vault

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
    assert not (s.data_dir / "seen.sqlite").exists()


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
