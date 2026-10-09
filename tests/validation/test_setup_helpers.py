import socket
import types
from decimal import Decimal

from template_core.config import load_config
from template_core.pricing import load_pricing
from validation.cli import select_gates
from validation import framework, gates_setup
from validation.gates_setup import port_in_use
from validation.spend import g7_spec, scale_prices, validation_spend_estimate


def test_select_gates():
    assert select_gates("all", None)[0] == "g0" and select_gates("all", None)[-1] == "g10"
    assert select_gates("all", "g9")[-1] == "g9"


def test_select_single_gate_g11():
    assert select_gates("g11", None) == ["g11"]


def test_g7_spec_uses_tools_agent_first_three_models_two_repeats():
    cfg = load_config()
    spec = g7_spec(cfg)
    assert spec.agent == "tools-agent" and spec.models == cfg.agents["tools-agent"].models[:3] and spec.repeats == 2
    assert len(spec.suite.cases) == 3


def test_spend_estimate_is_under_budget_and_scaling_doubles():
    cfg, prices = load_config(), load_pricing()
    assert Decimal(0) < validation_spend_estimate(cfg, prices) < Decimal(5)
    doubled = scale_prices(prices, 2)
    assert doubled.models["haiku-4-5"].input == prices.models["haiku-4-5"].input * 2


def test_port_in_use_detects_listener():
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        assert port_in_use(srv.getsockname()[1])


def test_g2_popen_gets_new_session_kwargs_on_posix(tmp_path, monkeypatch):
    seen = {}

    class FakeProc:
        pid = 5

        def __init__(self, *a, **kw):
            seen.update(kw)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(framework, "os", types.SimpleNamespace(name="posix"))
    monkeypatch.setattr(gates_setup.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(gates_setup, "port_in_use", lambda port: False)
    monkeypatch.setattr(gates_setup, "wait_for_ping", lambda port, timeout: False)
    monkeypatch.setattr(gates_setup, "kill_tree", lambda pid: None)
    ctx = framework.GateContext(load_config(), load_pricing(), None, True, tmp_path)
    checks = gates_setup.g2(ctx)
    assert seen.get("start_new_session") is True
    assert checks and all(not c.ok for c in checks)


def test_kit_lock_check_compares_committed_lock_with_fresh_export(tmp_path):
    import subprocess

    lock = tmp_path / "requirements.lock"
    lock.write_bytes(b"boto3==1.0\r\nstrands-agents==1.59.0\r\n")  # CRLF from a Windows checkout still matches
    fresh = subprocess.CompletedProcess([], 0, "boto3==1.0\nstrands-agents==1.59.0\n", "")
    assert gates_setup.kit_lock_check(lock, fresh).ok
    stale = subprocess.CompletedProcess([], 0, "boto3==1.1\nstrands-agents==1.59.0\n", "")
    bad = gates_setup.kit_lock_check(lock, stale)
    assert not bad.ok and "boto3==1.1" in bad.detail and "uv export" in bad.detail
    assert not gates_setup.kit_lock_check(tmp_path / "missing.lock", fresh).ok
    assert not gates_setup.kit_lock_check(lock, subprocess.CompletedProcess([], 2, "", "lock out of date")).ok


def test_kit_lock_export_flags():
    assert gates_setup.KIT_LOCK_EXPORT[:2] == ["uv", "export"]
    for flag in ("--frozen", "--no-hashes", "--no-emit-workspace", "--no-dev", "--no-header"):
        assert flag in gates_setup.KIT_LOCK_EXPORT
