import json
import subprocess
import types

import pytest

from validation import framework

from template_core.config import load_config
from template_core.pricing import load_pricing
from validation.framework import (
    GATES,
    Check,
    GateContext,
    command_check,
    gate,
    latest_state,
    run_gates,
    tail,
    within,
)


@pytest.fixture
def ctx(tmp_path):
    return GateContext(load_config(), load_pricing(), None, True, tmp_path / "run")


@pytest.fixture
def temp_gates():
    saved = dict(GATES)
    yield
    GATES.clear()
    GATES.update(saved)


def test_gates_stop_at_first_failure_and_write_evidence(ctx, temp_gates):
    calls = []

    @gate("t1", "passes with a warning")
    def _t1(c):
        calls.append("t1")
        c.state["seen"] = True
        return [Check("fine", True), Check("tags", False, "inactive", warning=True)]

    @gate("t2", "fails")
    def _t2(c):
        calls.append("t2")
        return [Check("broken", False, "nope")]

    @gate("t3", "never runs")
    def _t3(c):
        calls.append("t3")
        return []

    lines = []
    results = run_gates(["t1", "t2", "t3"], ctx, echo=lines.append)
    assert calls == ["t1", "t2"] and [r.passed for r in results] == [True, False]
    assert any("[WARN] tags" in line for line in lines)
    assert (ctx.out_dir / "t1.json").exists() and (ctx.out_dir / "summary.md").exists()
    assert json.loads((ctx.out_dir / "state.json").read_text())["seen"] is True
    assert "| t2 | fails | FAIL |" in (ctx.out_dir / "summary.md").read_text()


def test_crashing_gate_is_a_failure(ctx, temp_gates):
    @gate("t9", "crashes")
    def _t9(c):
        raise RuntimeError("kaboom")

    results = run_gates(["t9"], ctx, echo=lambda s: None)
    assert not results[0].passed and "kaboom" in results[0].checks[0].detail


def test_latest_state_picks_newest(tmp_path):
    for name, value in (("20261001T000000Z", 1), ("20261009T000000Z", 2)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "state.json").write_text(json.dumps({"v": value}))
    assert latest_state(tmp_path) == {"v": 2}
    assert latest_state(tmp_path / "missing") == {}


def test_latest_state_ignores_other_profile_region_prefix(tmp_path):
    def put(name, **kw):
        (tmp_path / name).mkdir()
        (tmp_path / name / "state.json").write_text(json.dumps(kw))

    put("20261001T000000Z", profile="p", region="r", prefix="a", v=1)
    put("20261009T000000Z", profile="p", region="r", prefix="b", v=2)
    assert latest_state(tmp_path, "p", "r", "a")["v"] == 1
    assert latest_state(tmp_path, "p", "r", "b")["v"] == 2
    assert latest_state(tmp_path, "p", "x", "a") == {}


def test_run_kills_process_tree_on_timeout(ctx, monkeypatch):
    killed = []

    class FakeProc:
        pid = 4242
        returncode = None

        def __init__(self, *a, **kw):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("x", timeout)
            return "partial", ""

    monkeypatch.setattr(framework.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(framework, "kill_tree", killed.append)
    proc = ctx.run(["python", "-c", "pass"], timeout=1)
    assert killed == [4242] and proc.returncode == -1 and "timed out" in proc.stderr


def test_confirm_without_tty_is_false(tmp_path, monkeypatch, capsys):
    c = GateContext(load_config(), load_pricing(), None, False, tmp_path)

    def eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert c.confirm("ok?") is False and "pass --yes" in capsys.readouterr().out


def test_helpers():
    assert tail("\n".join(str(i) for i in range(100)), 3) == "97\n98\n99"
    assert within(102, 100, 0.02) and not within(103, 100, 0.02) and within(0, 0, 0.02)
    ok = command_check("x", subprocess.CompletedProcess(["x"], 0, "out", ""))
    bad = command_check("x", subprocess.CompletedProcess(["x"], 1, "", "err"))
    assert ok.ok and not bad.ok and "err" in bad.detail


@pytest.mark.parametrize(("os_name", "expected"), [("posix", True), ("nt", False)])
def test_children_get_own_session_on_posix_only(ctx, monkeypatch, os_name, expected):
    seen = {}

    class FakeProc:
        pid = 1
        returncode = 0

        def __init__(self, *a, **kw):
            seen.update(kw)

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(framework.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(framework, "os", types.SimpleNamespace(name=os_name))
    ctx.run(["python", "-c", "pass"])
    assert seen.get("start_new_session", False) is expected
    assert framework.new_session_kwargs() == ({"start_new_session": True} if expected else {})


def test_keyboard_interrupt_kills_child_tree_and_reraises(ctx, monkeypatch):
    killed = []

    class FakeProc:
        pid = 77

        def __init__(self, *a, **kw):
            pass

        def communicate(self, timeout=None):
            raise KeyboardInterrupt

    monkeypatch.setattr(framework.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(framework, "kill_tree", killed.append)
    with pytest.raises(KeyboardInterrupt):
        ctx.run(["python", "-c", "pass"])
    assert killed == [77]
