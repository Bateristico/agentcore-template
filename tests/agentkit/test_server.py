import json
from types import SimpleNamespace

import pytest

from agentkit.server import handle, load_agent_module

ENV = {"MODEL_PROFILES": json.dumps({"m1": "id-1", "m2": "id-2"}), "DEFAULT_MODEL": "m1"}


def fake_metrics(inp, out, cycles=1):
    return SimpleNamespace(
        accumulated_usage={"inputTokens": inp, "outputTokens": out, "totalTokens": inp + out},
        cycle_count=cycles,
        tool_metrics={},
    )


class Result:
    def __init__(self, text, metrics):
        self.text, self.metrics = text, metrics

    def __str__(self):
        return self.text


class OkAgent:
    def __init__(self, model):
        self.model = model

    def __call__(self, prompt):
        return Result(f"echo: {prompt}", fake_metrics(100, 20, cycles=2))


class FailingAgent:
    """Fails mid-call after one model response: partial usage lives on event_loop_metrics."""

    def __init__(self, model):
        self.model = model
        self.event_loop_metrics = fake_metrics(11, 0)

    def __call__(self, prompt):
        raise RuntimeError("ThrottlingException: slow down")


def run(payload, *, fail=False, built=None):
    lines = []

    def build_agent(model):
        if built is not None:
            built.append(model)
        return FailingAgent(model) if fail else OkAgent(model)

    out = handle(
        payload,
        build_agent=build_agent,
        agent_name="tools-agent",
        session_id="s" * 40,
        env=ENV,
        model_factory=lambda choice: choice,
        write=lines.append,
        clock=iter([1.0, 1.25]).__next__,
    )
    return out, [json.loads(line) for line in lines]


def test_success_returns_output_usage_and_emits_emf():
    built = []
    out, emf = run({"prompt": "hi", "model": "m2", "run_id": "r1", "case_id": "c1"}, built=built)
    assert out["error"] is None and out["output"] == "echo: hi"
    assert out["usage"] == {
        "model": "m2", "input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "latency_ms": 250, "cycles": 2, "tool_calls": 0,
    }
    assert built[0].model_id == "id-2"
    assert len(emf) == 1 and emf[0]["Errors"] == 0 and emf[0]["Model"] == "m2" and emf[0]["run_id"] == "r1"


def test_default_model_when_not_requested():
    out, _ = run({"prompt": "hi"})
    assert out["usage"]["model"] == "m1"


def test_disallowed_model_errors_without_building_agent_or_emitting():
    built = []
    out, emf = run({"prompt": "hi", "model": "gpt"}, built=built)
    assert out["error"]["type"] == "ModelNotAllowed" and out["error"]["allowed"] == ["m1", "m2"]
    assert out["usage"] is None and built == [] and emf == []


@pytest.mark.parametrize("payload", [None, "hi", {}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 3}])
def test_bad_payload(payload):
    out, emf = run(payload)
    assert out["error"]["type"] == "BadRequest" and emf == []


def test_agent_failure_reports_partial_usage_and_error_metric():
    out, emf = run({"prompt": "hi"}, fail=True)
    assert out["output"] is None
    assert out["error"]["type"] == "RuntimeError" and "ThrottlingException" in out["error"]["message"]
    assert out["usage"]["input_tokens"] == 11
    assert emf[0]["Errors"] == 1


def test_misconfigured_runtime_is_reported():
    out = handle({"prompt": "hi"}, build_agent=lambda m: None, agent_name="a", env={}, write=lambda s: None)
    assert out["error"]["type"] == "ServerMisconfigured"


def test_load_agent_module_imports_sibling_files(tmp_path):
    (tmp_path / "helper.py").write_text("VALUE = 42\n")
    (tmp_path / "agent.py").write_text("from helper import VALUE\n\ndef build_agent(model):\n    return VALUE\n")
    assert load_agent_module(tmp_path).build_agent(None) == 42


def test_load_agent_module_requires_build_agent(tmp_path):
    (tmp_path / "agent.py").write_text("x = 1\n")
    with pytest.raises(Exception, match="build_agent"):
        load_agent_module(tmp_path)
