from datetime import date

from agentkit.metering import Usage
from template_core.agentcore import InvokeResult
from template_core.cli_invoke import format_result, main
from template_core.deployment import DeploymentNotFound
from template_core.pricing import PriceTable

PRICES = PriceTable.model_validate(
    {"as_of": date(2026, 10, 9), "agentcore": {"vcpu_hour": 1, "gb_hour": 1}, "models": {"m1": {"input": 1.0, "output": 5.0}}}
)


def test_format_success():
    usage = Usage("m1", input_tokens=1000, output_tokens=200, latency_ms=900, cycles=2, tool_calls=1)
    text = format_result(InvokeResult("a", "m1", "sid", "the answer", usage, None, 950), PRICES)
    assert text.startswith("the answer")
    assert "model=m1 in=1000 out=200" in text and "tools=1" in text
    assert "cost=$0.002000" in text and "session=sid" in text


def test_format_error_and_unpriced():
    usage = Usage("m9", input_tokens=5)
    text = format_result(InvokeResult("a", "m9", "sid", None, usage, {"type": "X", "message": "boom"}, 10), PRICES)
    assert "ERROR X: boom" in text and "cost=unpriced" in text


def test_deployment_not_found_exits_2(monkeypatch, capsys):
    def boom(cfg, session):
        raise DeploymentNotFound("x")

    monkeypatch.setattr("template_core.cli_invoke.load_deployment", boom)
    rc = main(["--agent", "tools-agent", "--prompt", "hi"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "invoke:" in captured.err and "x" in captured.err
