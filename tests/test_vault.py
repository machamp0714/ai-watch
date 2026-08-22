from datetime import date

from ai_watch.vault import Vault


def test_paths_and_atomic_write(tmp_path):
    v = Vault(tmp_path / "ai-watch")
    p = v.digest_path(date(2026, 8, 22))
    assert p == tmp_path / "ai-watch" / "digests" / "2026-08-22.md"
    v.write_atomic(p, "hello")
    assert p.read_text() == "hello" and not p.with_suffix(".md.tmp").exists()
    assert v.read_digest(date(2026, 8, 22)) == "hello"
    assert v.read_digest(date(2026, 8, 23)) is None


def test_recent_digests_excludes_today_and_missing(tmp_path):
    v = Vault(tmp_path)
    for d in ("2026-08-20", "2026-08-21", "2026-08-22"):
        v.write_atomic(v.digests / f"{d}.md", d)
    got = v.recent_digests(date(2026, 8, 22), days=7)
    assert [(d.isoformat(), t) for d, t in got] == [("2026-08-21", "2026-08-21"), ("2026-08-20", "2026-08-20")]


def test_insert_under_heading(tmp_path):
    v = Vault(tmp_path)
    p = tmp_path / "outputs.md"
    p.write_text("# Outputs\n\n## 投稿待ち\n\n## 投稿済み\n- old\n")
    assert v.insert_under_heading(p, "## 投稿待ち", "- new ^aw-1", dedupe_key="^aw-1") is True
    assert v.insert_under_heading(p, "## 投稿待ち", "- new again ^aw-1", dedupe_key="^aw-1") is False
    assert p.read_text() == "# Outputs\n\n## 投稿待ち\n- new ^aw-1\n\n## 投稿済み\n- old\n"


def test_insert_under_missing_heading_appends(tmp_path):
    v = Vault(tmp_path)
    p = tmp_path / "backlog.md"
    p.write_text("# Backlog\n")
    v.insert_under_heading(p, "## 候補", "- x")
    assert p.read_text() == "# Backlog\n\n## 候補\n- x\n"


def test_append_line_and_profile(tmp_path):
    v = Vault(tmp_path)
    v.append_line(v.log, "## [2026-08-22] nightly | ok")
    v.append_line(v.log, "## [2026-08-23] nightly | ok")
    assert v.log.read_text().splitlines()[-1] == "## [2026-08-23] nightly | ok"
    assert v.read_profile() == ""
    v.profile.write_text("# Profile")
    assert v.read_profile() == "# Profile"


def test_init_vault_creates_missing_only(tmp_path):
    from ai_watch.vault import init_vault
    tpl = tmp_path / "templates" / "vault"
    tpl.mkdir(parents=True)
    for name in ("CLAUDE.md", "profile.md", "backlog.md", "outputs.md", "log.md"):
        (tpl / name).write_text(f"T:{name}")
    v = Vault(tmp_path / "vault")
    v.write_atomic(v.profile, "my profile")
    created = init_vault(v, tpl)
    assert {p.name for p in created} == {"CLAUDE.md", "backlog.md", "outputs.md", "log.md", "digests", "experiments"}
    assert v.profile.read_text() == "my profile"           # 既存は守る
    assert v.backlog.read_text() == "T:backlog.md"
    assert v.digests.is_dir() and v.experiments.is_dir()
    assert init_vault(v, tpl) == []
