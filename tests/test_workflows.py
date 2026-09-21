import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict:
    return yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def test_nightly_has_schedule_manual_trigger_and_single_writer():
    workflow = _workflow("nightly.yml")

    assert workflow["on"]["schedule"] == [{"cron": "0 20 * * *"}]
    assert workflow["on"]["workflow_dispatch"] == ""
    assert workflow["concurrency"] == {
        "group": "ai-watch-nightly",
        "cancel-in-progress": "false",
    }
    assert workflow["permissions"] == {}
    assert workflow["jobs"]["nightly"]["permissions"] == {"contents": "read"}


def test_nightly_runs_pull_then_pipeline_then_push_without_x():
    workflow = _workflow("nightly.yml")
    steps = _steps(workflow, "nightly")
    commands = [step.get("run", "") for step in steps]
    pull = next(i for i, command in enumerate(commands) if "ai-watch-r2 pull" in command)
    nightly = next(i for i, command in enumerate(commands) if "ai-watch nightly" in command)
    push = next(i for i, command in enumerate(commands) if "ai-watch-r2 push-results" in command)

    assert pull < nightly < push
    assert "--skip-x-collect" in commands[nightly]
    assert "$RUNNER_TEMP/nightly-report.json" in commands[nightly]
    assert "if" not in steps[push]

    all_commands = "\n".join(commands)
    assert "--delete" not in all_commands
    assert "create-watchlist" not in all_commands
    assert "update-watchlist" not in all_commands
    assert "config/" not in commands[push]


def test_nightly_limits_secrets_to_required_steps_and_does_not_publish_results():
    workflow = _workflow("nightly.yml")
    steps = _steps(workflow, "nightly")
    secret_uses = {
        step["name"]: "\n".join((step.get("env") or {}).values())
        for step in steps
        if step.get("env")
    }

    assert "secrets.ANTHROPIC_API_KEY" in secret_uses["nightlyを実行"]
    assert "secrets.R2_ACCESS_KEY_ID" not in secret_uses["nightlyを実行"]
    for name in ("R2から取得", "R2へ成果物を保存"):
        assert "secrets.R2_ACCESS_KEY_ID" in secret_uses[name]
        assert "secrets.R2_SECRET_ACCESS_KEY" in secret_uses[name]
        assert "secrets.ANTHROPIC_API_KEY" not in secret_uses[name]

    document = (WORKFLOWS / "nightly.yml").read_text(encoding="utf-8")
    assert "upload-artifact" not in document
    assert "cat $RUNNER_TEMP/nightly-report.json" not in document
    assert re.search(r"\b(cat|sed|head|tail)\b.*watchlist", document) is None


def test_nightly_failure_notification_contains_only_run_location():
    workflow = _workflow("nightly.yml")
    notify = workflow["jobs"]["notify-failure"]

    assert notify["needs"] == "nightly"
    assert "needs.nightly.result != 'success'" in notify["if"]
    assert notify["permissions"] == {"issues": "write"}
    command = _steps(workflow, "notify-failure")[0]["run"]
    assert "gh issue" in command
    assert "RUN_URL" in command
    assert "nightly-report" not in command
    assert "watchlist" not in command
    assert "vault" not in command


def test_keepalive_makes_monthly_activity_with_one_tracked_file():
    workflow = _workflow("keepalive.yml")

    assert workflow["on"]["schedule"] == [{"cron": "17 3 1 * *"}]
    assert workflow["on"]["workflow_dispatch"] == ""
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["keepalive"]
    assert job["permissions"] == {"contents": "write"}
    command = "\n".join(step.get("run", "") for step in job["steps"])
    assert "git add .github/keepalive" in command
    assert "git add ." not in {line.strip() for line in command.splitlines()}
    assert "git push origin HEAD:main" in command


def test_production_workflow_replaces_temporary_api_probe():
    assert not (WORKFLOWS / "claude-api-probe.yml").exists()


def test_ci_runs_python_and_worker_verification_without_secrets():
    workflow = _workflow("ci.yml")

    assert workflow["on"]["pull_request"] == ""
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert workflow["permissions"] == {"contents": "read"}

    python_steps = _steps(workflow, "python")
    python_commands = "\n".join(step.get("run", "") for step in python_steps)
    assert "uv sync --frozen" in python_commands
    assert "uv run pytest -q" in python_commands

    worker_steps = _steps(workflow, "worker")
    worker_commands = "\n".join(step.get("run", "") for step in worker_steps)
    assert "npm ci" in worker_commands
    assert "npm run test:worker" in worker_commands
    assert "wrangler deploy --dry-run" in worker_commands

    for job in workflow["jobs"].values():
        for step in job["steps"]:
            assert "secrets." not in repr(step)
            if "uses" in step:
                _, reference = step["uses"].rsplit("@", 1)
                assert re.fullmatch(r"[0-9a-f]{40}", reference.split()[0])


def test_retriage_is_manual_single_writer_and_does_not_touch_seen_or_config():
    workflow = _workflow("retriage.yml")
    triggers = workflow["on"]
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["concurrency"]["group"] == _workflow("nightly.yml")["concurrency"]["group"]
    steps = _steps(workflow, "retriage")
    commands = [step.get("run", "") for step in steps]
    pull = next(i for i, c in enumerate(commands) if "ai-watch-r2 pull" in c)
    run = next(i for i, c in enumerate(commands) if "ai-watch nightly" in c)
    push = next(i for i, c in enumerate(commands) if "ai-watch-r2 push-results" in c)
    assert pull < run < push
    assert "--from triage --retriage --skip-x-collect" in commands[run]
    assert "${{" not in commands[run]                       # 入力は env 経由でのみ受け取る
    assert "inputs.dates" in steps[run]["env"]["DATES"]
    assert "secrets.R2_ACCESS_KEY_ID" not in "\n".join(steps[run]["env"].values())
    all_commands = "\n".join(commands)
    assert "--delete" not in all_commands and "watchlist" not in all_commands.replace("AI_WATCH_WATCHLIST_FILE", "")
