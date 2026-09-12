from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema


@dataclass
class ClaudeResult:
    ok: bool
    data: dict | None
    cost_usd: float
    subtype: str
    error: str = ""
    raw: dict = field(default_factory=dict)


def build_command(
    claude_bin: str, model: str, schema: dict, *, budget_usd: float, effort: str,
    mcp_config: Path | None, allowed_tools: list[str] | None,
) -> list[str]:
    # --bare is avoided because it disables keychain/OAuth authentication.
    # Instead, we use isolation flags to disable plugins, session persistence, and slash commands.
    cmd = [
        claude_bin, "-p", "--no-session-persistence", "--setting-sources", "", "--disable-slash-commands",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--max-budget-usd", str(budget_usd),
        "--model", model,
        "--effort", effort,
    ]
    if mcp_config is None:
        cmd += ["--tools", ""]  # 組み込みツールを全部切る：純粋な判定呼び出し
    else:
        cmd += [
            "--mcp-config", str(mcp_config), "--strict-mcp-config",
            "--allowedTools", ",".join(allowed_tools or []),
            "--permission-mode", "dontAsk",
        ]
    return cmd


class ClaudeRunner:
    """`claude -p`をsubprocessで呼ぶ唯一の場所。認証方法は実行環境へ委ねる。"""

    def __init__(self, claude_bin: str = "claude", model: str = "sonnet", cwd: Path | None = None,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.claude_bin = claude_bin
        self.model = model
        self.cwd = cwd
        self._run = run

    def run(
        self, prompt: str, schema: dict[str, Any], *, budget_usd: float,
        mcp_config: Path | None = None, allowed_tools: list[str] | None = None,
        effort: str = "low", retries: int = 1, timeout_s: int = 900,
    ) -> ClaudeResult:
        cmd = build_command(self.claude_bin, self.model, schema, budget_usd=budget_usd, effort=effort,
                            mcp_config=mcp_config, allowed_tools=allowed_tools)
        total_cost = 0.0
        last_error = ""
        last_subtype = ""

        def _log_failure(attempt_no: int, proc: subprocess.CompletedProcess | None) -> None:
            stderr_part = ""
            if proc is not None and proc.stderr:
                stderr_part = f" | stderr: {proc.stderr[-500:]}"
            print(f"[claude_runner] attempt {attempt_no} failed ({last_subtype or 'n/a'}): {last_error}{stderr_part}",
                  file=sys.stderr)

        for attempt in range(retries + 1):
            proc = None
            try:
                proc = self._run(cmd, input=prompt, capture_output=True, text=True,
                                 timeout=timeout_s, cwd=self.cwd)
            except subprocess.TimeoutExpired:
                last_error = "timeout"
                last_subtype = "timeout"
                _log_failure(attempt + 1, proc)
                continue
            except OSError as e:
                last_error = f"spawn failed: {e}"
                last_subtype = "spawn_error"
                _log_failure(attempt + 1, proc)
                break  # バイナリが無い等はリトライしても無駄
            try:
                payload = json.loads(proc.stdout)
            except json.JSONDecodeError:
                last_error = f"non-json stdout (rc={proc.returncode}): {proc.stderr[-300:]}"
                last_subtype = "non_json"
                _log_failure(attempt + 1, proc)
                continue
            if not isinstance(payload, dict):
                last_error = "non-dict json"
                last_subtype = "non_json"
                _log_failure(attempt + 1, proc)
                continue
            total_cost += float(payload.get("total_cost_usd") or 0.0)
            last_subtype = str(payload.get("subtype", ""))
            data = payload.get("structured_output")
            if last_subtype == "success" and isinstance(data, dict):
                try:
                    jsonschema.validate(data, schema)
                except jsonschema.ValidationError as e:
                    last_error = f"schema violation: {e.message}"[:300]
                    _log_failure(attempt + 1, proc)
                    continue
                return ClaudeResult(True, data, total_cost, last_subtype, raw=payload)
            last_error = f"{last_subtype}: {payload.get('errors') or payload.get('result', '')}"[:300]
            _log_failure(attempt + 1, proc)
            if last_subtype == "error_max_budget_usd":
                break  # 予算超過はリトライしても同じ
        return ClaudeResult(False, None, total_cost, last_subtype, error=last_error)
