"""`uv run validate <gate|all> [--until gN] [--yes] --profile P` (spec §6)."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from template_core.config import DEFAULT_CONFIG, load_config
from template_core.console import utf8_stdout
from template_core.pricing import load_pricing
from validation import gates_setup  # noqa: F401  (registers g0-g2)
from validation.framework import GATES, ORDER, VALIDATION_ROOT, GateContext, latest_state, run_gates

try:  # later tasks add these modules
    from validation import gates_bench, gates_deploy, gates_final  # noqa: F401
except ImportError:  # pragma: no cover - only before Tasks 17-18 exist
    pass


def select_gates(gate: str, until: str | None) -> list[str]:
    if gate == "all":
        return ORDER[: ORDER.index(until) + 1] if until else list(ORDER)
    if gate not in GATES:
        raise SystemExit(f"unknown gate '{gate}'; choose all or one of: {', '.join(sorted(GATES, key=lambda g: int(g[1:])))}")
    return [gate]


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(prog="validate", description=__doc__)
    ap.add_argument("gate", help="all, or a single gate such as g4")
    ap.add_argument("--until", choices=ORDER, help="with 'all': stop after this gate (e.g. g9 to keep the stacks)")
    ap.add_argument("--profile")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--yes", action="store_true", help="answer yes to confirmations (deploy, spend, destroy)")
    args = ap.parse_args(argv)

    ids = select_gates(args.gate, args.until)
    out_dir = VALIDATION_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    cfg = load_config(args.config)
    profile = args.profile or "default"
    state = latest_state(VALIDATION_ROOT, profile, cfg.region, cfg.prefix)
    state.update({"profile": profile, "region": cfg.region, "prefix": cfg.prefix})
    ctx = GateContext(cfg, load_pricing(), args.profile, args.yes, out_dir, state=state)
    results = run_gates(ids, ctx)
    print(f"evidence: {out_dir}")
    return 0 if len(results) == len(ids) and all(r.passed for r in results) else 1
