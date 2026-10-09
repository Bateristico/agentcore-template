"""Gate registry and runner. Each run writes evidence to validation/<timestamp>/ (spec §6)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3

from template_core.config import REPO_ROOT, DeployConfig
from template_core.pricing import PriceTable

VALIDATION_ROOT = REPO_ROOT / "validation"
ORDER = ["g0", "g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8", "g9", "g10"]  # g11 runs on its own, 24-48 h later


def kill_tree(pid: int) -> None:
    """Kill a process and all its children (uv spawns the real server as a child)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    warning: bool = False  # a failed warning is reported but never fails the gate


@dataclass
class GateResult:
    gate: str
    title: str
    checks: list[Check]
    started_at: str
    finished_at: str

    @property
    def passed(self) -> bool:
        return all(c.ok or c.warning for c in self.checks)


@dataclass
class GateContext:
    cfg: DeployConfig
    prices: PriceTable
    profile: str | None
    yes: bool
    out_dir: Path
    state: dict[str, Any] = field(default_factory=dict)
    repo_root: Path = REPO_ROOT
    _session: Any = None

    @property
    def session(self):
        if self._session is None:
            self._session = boto3.Session(profile_name=self.profile, region_name=self.cfg.region)
        return self._session

    def client(self, name: str, region: str | None = None):
        return self.session.client(name, region_name=region or self.cfg.region)

    def confirm(self, question: str) -> bool:
        if self.yes:
            return True
        try:
            return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            print("no TTY: pass --yes")
            return False

    def run(self, cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
        exe = shutil.which(cmd[0]) or cmd[0]  # resolves npx.cmd / uv.exe on Windows
        proc = subprocess.Popen(
            [exe, *cmd[1:]], cwd=cwd or self.repo_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            out, err = proc.communicate()
            return subprocess.CompletedProcess(cmd, -1, out or "", (err or "") + f"\ntimed out after {timeout} s")
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

    def cdk(self, *args: str, context: dict[str, str] | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
        cmd = ["npx", "cdk", *args]
        for key, value in (context or {}).items():
            cmd += ["-c", f"{key}={value}"]
        if self.profile:
            cmd += ["--profile", self.profile]
        return self.run(cmd, cwd=self.repo_root / "infra", timeout=timeout)

    def save_state(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "state.json").write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")


Gate = Callable[[GateContext], list[Check]]
GATES: dict[str, tuple[str, Gate]] = {}


def gate(gate_id: str, title: str) -> Callable[[Gate], Gate]:
    def register(fn: Gate) -> Gate:
        GATES[gate_id] = (title, fn)
        return fn

    return register


def tail(text: str, n: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def command_check(name: str, proc: subprocess.CompletedProcess) -> Check:
    return Check(name, proc.returncode == 0, tail((proc.stdout or "") + "\n" + (proc.stderr or "")))


def within(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-12) if b else a == b


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_evidence(out_dir: Path, results: list[GateResult]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Validation evidence", "", "| gate | title | result | checks |", "|---|---|---|---|"]
    for r in results:
        (out_dir / f"{r.gate}.json").write_text(json.dumps({**asdict(r), "passed": r.passed}, indent=2, default=str), encoding="utf-8")
        lines.append(f"| {r.gate} | {r.title} | {'PASS' if r.passed else 'FAIL'} | {sum(c.ok for c in r.checks)}/{len(r.checks)} ok |")
    for r in results:
        lines += ["", f"## {r.gate} — {r.title}", ""]
        for c in r.checks:
            status = "PASS" if c.ok else ("WARN" if c.warning else "FAIL")
            first = (c.detail.splitlines() or [""])[0][:200]
            lines.append(f"- **{status}** {c.name}: {first}")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_gates(ids: list[str], ctx: GateContext, echo: Callable[[str], Any] = print) -> list[GateResult]:
    results: list[GateResult] = []
    for gate_id in ids:
        title, fn = GATES[gate_id]
        echo(f"== {gate_id.upper()} {title}")
        started = _now()
        try:
            checks = fn(ctx)
        except Exception as exc:  # noqa: BLE001 - a crashing gate is a failed gate with evidence
            checks = [Check("gate crashed", False, f"{type(exc).__name__}: {exc}")]
        result = GateResult(gate_id, title, checks, started, _now())
        results.append(result)
        for c in checks:
            status = "PASS" if c.ok else ("WARN" if c.warning else "FAIL")
            echo(f"  [{status}] {c.name}: {(c.detail.splitlines() or [''])[0][:160]}")
        write_evidence(ctx.out_dir, results)
        ctx.save_state()
        if not result.passed:
            echo(f"{gate_id} failed; stopping. Evidence: {ctx.out_dir}")
            break
    return results


def latest_state(root: Path = VALIDATION_ROOT, profile: str | None = None, region: str | None = None,
                 prefix: str | None = None) -> dict[str, Any]:
    """Newest state.json whose profile/region/prefix match (states from other targets are ignored)."""
    if not root.is_dir():
        return {}
    want = {"profile": profile, "region": region, "prefix": prefix}
    for run in sorted((p for p in root.iterdir() if (p / "state.json").exists()), key=lambda p: p.name, reverse=True):
        state = json.loads((run / "state.json").read_text(encoding="utf-8"))
        if all(state.get(k) == v for k, v in want.items()):
            return state
    return {}
