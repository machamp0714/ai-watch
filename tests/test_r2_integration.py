import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_watch.r2_cli import main


VALID_ENV = {
    "R2_ACCESS_KEY_ID": "test-access-marker",
    "R2_SECRET_ACCESS_KEY": "test-secret-marker",
    "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    "R2_BUCKET_NAME": "example-private-bucket",
}


@pytest.fixture
def fake_aws(tmp_path, monkeypatch):
    executable = tmp_path / "bin/aws"
    executable.parent.mkdir()
    shutil.copy(Path(__file__).with_name("fake_aws.py"), executable)
    executable.chmod(0o755)
    r2_root = tmp_path / "r2"
    log = tmp_path / "aws-calls.jsonl"
    monkeypatch.setenv(
        "PATH",
        str(executable.parent) + os.pathsep + os.environ.get("PATH", ""),
    )
    monkeypatch.setenv("FAKE_R2_ROOT", str(r2_root))
    monkeypatch.setenv("FAKE_AWS_LOG", str(log))
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    return r2_root, log


def test_fake_aws_reports_supported_v2(fake_aws):
    result = subprocess.run(
        ["aws", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("aws-cli/2.27.20")


def write_file(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_pull_and_push_results_round_trip_only_allowed_files(fake_aws):
    r2_root, log = fake_aws
    write_file(r2_root / "config/watchlist.yaml", "version: 1\ntools: []\n")
    write_file(r2_root / "vault/digests/example.md", "remote digest")
    write_file(r2_root / "data/seen.sqlite", b"remote seen")
    write_file(r2_root / "data/decisions.jsonl", "remote decision\n")
    write_file(r2_root / "data/work/2026-09-12/collect.json", "{}")
    write_file(r2_root / "data/raw/private.json", "private raw")
    write_file(
        r2_root / "data/playwright-profile/session.json",
        "private session",
    )
    write_file(r2_root / "data/unlisted.txt", "unlisted")
    runtime = r2_root.parent / "runtime"

    assert main(["pull", "--root", str(runtime)]) == 0

    assert (runtime / "config/watchlist.yaml").read_text() == "version: 1\ntools: []\n"
    assert (runtime / "vault/digests/example.md").read_text() == "remote digest"
    assert (runtime / "data/seen.sqlite").read_bytes() == b"remote seen"
    assert (runtime / "data/decisions.jsonl").read_text() == "remote decision\n"
    assert (runtime / "data/work/2026-09-12/collect.json").exists()
    assert not (runtime / "data/raw").exists()
    assert not (runtime / "data/playwright-profile").exists()
    assert not (runtime / "data/unlisted.txt").exists()

    write_file(runtime / "vault/digests/example.md", "local digest")
    write_file(runtime / "data/seen.sqlite", b"local seen")
    write_file(runtime / "data/work/2026-09-12/triage.json", "{}")
    write_file(runtime / "data/raw/local-private.json", "private raw")
    write_file(runtime / "data/unlisted-local.txt", "unlisted")
    write_file(r2_root / "vault/remote-only.md", "keep me")
    original_config = (r2_root / "config/watchlist.yaml").read_text()
    original_raw = (r2_root / "data/raw/private.json").read_text()

    assert main(["push-results", "--root", str(runtime)]) == 0

    assert (r2_root / "vault/digests/example.md").read_text() == "local digest"
    assert (r2_root / "vault/remote-only.md").read_text() == "keep me"
    assert (r2_root / "data/seen.sqlite").read_bytes() == b"local seen"
    assert (r2_root / "data/work/2026-09-12/triage.json").exists()
    assert (r2_root / "config/watchlist.yaml").read_text() == original_config
    assert (r2_root / "data/raw/private.json").read_text() == original_raw
    assert not (r2_root / "data/raw/local-private.json").exists()
    assert not (r2_root / "data/unlisted-local.txt").exists()

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    joined = "\n".join(" ".join(call) for call in calls)
    assert "test-access-marker" not in joined
    assert "test-secret-marker" not in joined
    assert "--delete" not in joined
    push_calls = [call for call in calls if "sync" in call][-2:]
    assert all("config/watchlist.yaml" not in " ".join(call) for call in push_calls)


def write_valid_watchlist_files(base: Path, name: str):
    sources = base / "sources.yaml"
    write_file(sources, "sources:\n  - id: example-feed\n    type: rss\n")
    watchlist = base / f"watchlist-{name}.yaml"
    write_file(
        watchlist,
        "version: 1\ntools:\n"
        f"  - id: {name}\n"
        f"    name: サンプル{name}\n"
        "    enabled: true\n"
        "    focus: [架空の更新]\n"
        "    source_ids: [example-feed]\n",
    )
    return watchlist, sources


def test_create_watchlist_refuses_second_registration(fake_aws):
    r2_root, _ = fake_aws
    runtime = r2_root.parent / "runtime"
    watchlist, sources = write_valid_watchlist_files(r2_root.parent, "example-a")
    argv = [
        "create-watchlist",
        "--root", str(runtime),
        "--file", str(watchlist),
        "--sources", str(sources),
    ]

    assert main(argv) == 0
    first = (r2_root / "config/watchlist.yaml").read_text()
    assert main(argv) == 3
    assert (r2_root / "config/watchlist.yaml").read_text() == first


def test_stale_etag_update_is_rejected_without_overwrite(fake_aws):
    r2_root, _ = fake_aws
    initial, sources = write_valid_watchlist_files(r2_root.parent, "initial")
    runtime_a = r2_root.parent / "runtime-a"
    runtime_b = r2_root.parent / "runtime-b"
    assert main([
        "create-watchlist", "--root", str(runtime_a),
        "--file", str(initial), "--sources", str(sources),
    ]) == 0
    assert main(["pull", "--root", str(runtime_a)]) == 0
    assert main(["pull", "--root", str(runtime_b)]) == 0
    update_b, _ = write_valid_watchlist_files(r2_root.parent, "update-b")
    stale_a, _ = write_valid_watchlist_files(r2_root.parent, "stale-a")

    assert main([
        "update-watchlist", "--root", str(runtime_b),
        "--file", str(update_b), "--sources", str(sources),
    ]) == 0
    expected = update_b.read_text()
    assert main([
        "update-watchlist", "--root", str(runtime_a),
        "--file", str(stale_a), "--sources", str(sources),
    ]) == 3
    assert (r2_root / "config/watchlist.yaml").read_text() == expected


def test_failed_get_keeps_existing_watchlist_and_hides_child_stderr(
    fake_aws, monkeypatch, capsys,
):
    r2_root, _ = fake_aws
    write_file(r2_root / "config/watchlist.yaml", "version: 1\ntools: []\n")
    runtime = r2_root.parent / "runtime"
    write_file(runtime / "config/watchlist.yaml", "old local")
    write_file(runtime / ".r2-state/watchlist-etag", '"old-etag"\n')
    monkeypatch.setenv("FAKE_AWS_FAIL_OPERATION", "get-object")

    assert main(["pull", "--root", str(runtime)]) == 1

    captured = capsys.readouterr()
    assert "test-secret-marker" not in captured.out + captured.err
    assert "fake-private-body" not in captured.out + captured.err
    assert (runtime / "config/watchlist.yaml").read_text() == "old local"
    assert (runtime / ".r2-state/watchlist-etag").read_text() == '"old-etag"\n'
    assert list((runtime / "config").glob(".watchlist.yaml.*")) == []
