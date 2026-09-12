from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Protocol

from .claude_runner import ClaudeResult, ClaudeRunner


PROBE_SCHEMA = {
    "type": "object",
    "properties": {"probe": {"type": "string", "const": "ok"}},
    "required": ["probe"],
    "additionalProperties": False,
}


class ProbeRunner(Protocol):
    def run(self, prompt: str, schema: dict, **kwargs) -> ClaudeResult: ...


class ProbeError(RuntimeError):
    pass


def verify_claude_api(runner: ProbeRunner) -> dict[str, object]:
    result = runner.run(
        "認証と構造化出力の疎通確認です。probeへokを設定してください。",
        PROBE_SCHEMA,
        budget_usd=0.2,
        effort="low",
        retries=0,
        timeout_s=300,
    )
    raw = result.raw
    cost = raw.get("total_cost_usd") if isinstance(raw, dict) else None
    if (
        not result.ok
        or result.subtype != "success"
        or result.data != {"probe": "ok"}
        or raw.get("subtype") != "success"
        or raw.get("structured_output") != {"probe": "ok"}
        or isinstance(cost, bool)
        or not isinstance(cost, (int, float))
    ):
        raise ProbeError("Claude APIの応答が必要な契約を満たしません")
    return {
        "subtype": "success",
        "structured_output": {"probe": "ok"},
        "total_cost_usd": float(cost),
    }


def main(runner: ProbeRunner | None = None) -> int:
    active_runner = runner or ClaudeRunner(model="sonnet", cwd=Path.cwd())
    try:
        summary = verify_claude_api(active_runner)
    except ProbeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
