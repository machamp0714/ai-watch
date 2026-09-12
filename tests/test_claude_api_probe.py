import json

import pytest

from ai_watch.claude_runner import ClaudeResult
from ai_watch.claude_api_probe import ProbeError, main, verify_claude_api


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, prompt, schema, **kwargs):
        self.calls.append((prompt, schema, kwargs))
        return self.result


def successful_result():
    data = {"probe": "ok"}
    return ClaudeResult(
        ok=True,
        data=data,
        cost_usd=0.002,
        subtype="success",
        raw={
            "subtype": "success",
            "structured_output": data,
            "total_cost_usd": 0.002,
        },
    )


def test_probe_uses_current_runner_contract_and_returns_safe_summary():
    runner = FakeRunner(successful_result())

    summary = verify_claude_api(runner)

    assert summary == {
        "subtype": "success",
        "structured_output": {"probe": "ok"},
        "total_cost_usd": 0.002,
    }
    _, schema, kwargs = runner.calls[0]
    assert schema["required"] == ["probe"]
    assert kwargs == {"budget_usd": 0.2, "effort": "low", "retries": 0, "timeout_s": 300}


@pytest.mark.parametrize(
    "result",
    [
        ClaudeResult(False, None, 0.0, "error", raw={}),
        ClaudeResult(True, {"probe": "ok"}, 0.0, "success", raw={}),
        ClaudeResult(
            True,
            {"probe": "wrong"},
            0.1,
            "success",
            raw={
                "subtype": "success",
                "structured_output": {"probe": "wrong"},
                "total_cost_usd": 0.1,
            },
        ),
    ],
)
def test_probe_rejects_incompatible_result_without_exposing_raw_payload(result):
    with pytest.raises(ProbeError) as captured:
        verify_claude_api(FakeRunner(result))
    assert "wrong" not in str(captured.value)


def test_probe_main_prints_only_compatible_summary(capsys):
    assert main(FakeRunner(successful_result())) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["subtype"] == "success"
    assert output["structured_output"] == {"probe": "ok"}
