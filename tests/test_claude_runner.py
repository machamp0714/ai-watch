import json
import subprocess
from pathlib import Path

from ai_watch.claude_runner import ClaudeRunner, build_command

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False}


class FakeRun:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def _ok(n, cost=0.05):
    return json.dumps({"type": "result", "subtype": "success", "structured_output": {"n": n}, "total_cost_usd": cost})


def test_build_command_without_mcp_disables_tools():
    cmd = build_command("claude", "sonnet", SCHEMA, budget_usd=2.0, effort="low", mcp_config=None, allowed_tools=None)
    assert cmd[:2] == ["claude", "-p"]
    assert "--bare" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--disable-slash-commands" in cmd
    assert "--no-session-persistence" in cmd and "--output-format" in cmd
    assert cmd[cmd.index("--json-schema") + 1] == json.dumps(SCHEMA)
    assert cmd[cmd.index("--max-budget-usd") + 1] == "2.0"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--mcp-config" not in cmd


def test_build_command_with_mcp():
    cmd = build_command("claude", "sonnet", SCHEMA, budget_usd=1.0, effort="medium",
                        mcp_config=Path("/c/mcp.json"), allowed_tools=["mcp__playwright__browser_navigate"])
    assert cmd[cmd.index("--mcp-config") + 1] == "/c/mcp.json"
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__playwright__browser_navigate"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--tools" not in cmd


def test_success_passes_prompt_on_stdin():
    fake = FakeRun([_ok(1)])
    r = ClaudeRunner(run=fake, cwd=Path("/w")).run("PROMPT", SCHEMA, budget_usd=2.0)
    assert r.ok and r.data == {"n": 1} and r.cost_usd == 0.05 and r.subtype == "success"
    kw = fake.calls[0][1]
    assert kw["input"] == "PROMPT"
    assert kw["text"] is True
    assert kw["capture_output"] is True
    assert kw["timeout"] == 900
    assert kw["cwd"] == Path("/w")


def test_retries_on_invalid_json_then_succeeds():
    fake = FakeRun(["not json", _ok(2)])
    r = ClaudeRunner(run=fake).run("p", SCHEMA, budget_usd=2.0, retries=1)
    assert r.ok and r.data == {"n": 2} and len(fake.calls) == 2


def test_schema_violation_is_failure():
    bad = json.dumps({"type": "result", "subtype": "success", "structured_output": {"n": "x"}, "total_cost_usd": 0.1})
    r = ClaudeRunner(run=FakeRun([bad, bad])).run("p", SCHEMA, budget_usd=2.0, retries=1)
    assert not r.ok and "schema" in r.error and r.cost_usd == 0.2      # コストは試行の合計


def test_budget_error_does_not_retry():
    over = json.dumps({"type": "result", "subtype": "error_max_budget_usd", "total_cost_usd": 0.18, "errors": ["budget"]})
    fake = FakeRun([over, _ok(9)])
    r = ClaudeRunner(run=fake).run("p", SCHEMA, budget_usd=0.05, retries=1)
    assert not r.ok and r.subtype == "error_max_budget_usd" and len(fake.calls) == 1


def test_timeout_is_failure():
    fake = FakeRun([subprocess.TimeoutExpired("claude", 1)])
    r = ClaudeRunner(run=fake).run("p", SCHEMA, budget_usd=2.0, retries=0)
    assert not r.ok and r.error == "timeout" and r.subtype == "timeout"


def test_mixed_failure_does_not_leak_success_subtype():
    bad = json.dumps({"type": "result", "subtype": "success", "structured_output": {"n": "x"}, "total_cost_usd": 0.1})
    fake = FakeRun([bad, subprocess.TimeoutExpired("claude", 1)])
    r = ClaudeRunner(run=fake).run("p", SCHEMA, budget_usd=2.0, retries=1)
    assert not r.ok and r.subtype == "timeout" and r.error == "timeout" and r.cost_usd == 0.1


def test_missing_binary_fails_without_retry():
    fake = FakeRun([FileNotFoundError("[Errno 2] No such file or directory: 'claude'")])
    r = ClaudeRunner(run=fake).run("p", SCHEMA, budget_usd=2.0, retries=1)
    assert not r.ok and r.subtype == "spawn_error" and len(fake.calls) == 1
    assert "spawn failed" in r.error


def test_non_dict_json_retries_then_succeeds():
    fake = FakeRun(["[]", _ok(3)])
    r = ClaudeRunner(run=fake).run("p", SCHEMA, budget_usd=2.0, retries=1)
    assert r.ok and r.data == {"n": 3} and len(fake.calls) == 2


def test_failed_attempt_is_logged_to_stderr(capfd):
    fake = FakeRun(["not json", _ok(2)])
    r = ClaudeRunner(run=fake).run("p", SCHEMA, budget_usd=2.0, retries=1)
    assert r.ok
    err = capfd.readouterr().err
    assert "[claude_runner] attempt 1 failed" in err
    assert "non_json" in err
