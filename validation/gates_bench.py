"""G6 judge calibration, G7 benchmark end-to-end, G8 UI (spec §6)."""

from __future__ import annotations

from decimal import Decimal

from streamlit.testing.v1 import AppTest

from bench.calibration import calibrate, load_calibration
from bench.judge import Judge
from bench.orchestrate import execute_run, rerender
from bench.report import evaluation_cost
from bench.runner import estimate_cost
from bench.store import RunStore
from template_core.agentcore import AgentClient
from template_core.cli_invoke import format_result
from template_core.deployment import load_deployment
from template_core.pricing import format_usd
from ui.context import OVERRIDE_KEY, build_context, result_metrics
from validation.framework import Check, GateContext, command_check, gate, within
from validation.gates_deploy import PONG
from validation.spend import g7_spec, scale_prices


def _judge(ctx: GateContext, deployment) -> Judge:
    return Judge(ctx.cfg.judge, deployment.judge_profile, ctx.client("bedrock-runtime"))


@gate("g6", "Judge calibration")
def g6(ctx: GateContext) -> list[Check]:
    judge = _judge(ctx, load_deployment(ctx.cfg, ctx.session))
    return [Check(f"calibration {r.id}", r.ok, r.detail) for r in calibrate(judge.grade, load_calibration())]


@gate("g7", "Benchmark end-to-end")
def g7(ctx: GateContext) -> list[Check]:
    deployment = load_deployment(ctx.cfg, ctx.session)
    spec = g7_spec(ctx.cfg)
    estimate = estimate_cost(spec, ctx.prices, ctx.cfg.judge)
    if not ctx.confirm(f"Run the G7 benchmark ({len(spec.tasks())} calls, ≈ {format_usd(estimate)})?"):
        return [Check("benchmark confirmed", False, "declined")]
    store = RunStore(s3=ctx.client("s3"), bucket=deployment.results_bucket)
    outcome = execute_run(
        spec, cfg=ctx.cfg, prices=ctx.prices, deployment=deployment, client=AgentClient.from_session(deployment, ctx.session),
        judge=_judge(ctx, deployment), store=store, estimate=estimate,
    )
    ctx.state["g7_run_id"] = spec.run_id
    rows = outcome.rows
    errors = [f"{r.model}/{r.case_id}#{r.repeat}: {r.error['type']}" for r in rows if r.error]
    unscored = [f"{r.model}/{r.case_id}#{r.repeat}: {r.judge_error}" for r in rows if r.score is None]
    checks = [
        Check("every call succeeded", not errors, "; ".join(errors) or f"{len(rows)} calls"),
        Check("every row scored by the judge", not unscored, "; ".join(unscored) or "all scored"),
    ]
    s3 = ctx.client("s3")
    for name in ("run.json", "results.jsonl", "report.html"):
        key = f"runs/{spec.run_id}/{name}"
        try:
            s3.head_object(Bucket=deployment.results_bucket, Key=key)
            checks.append(Check(f"s3 {name}", True, key))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(f"s3 {name}", False, f"{key}: {exc}"))

    base = {s.model: s.total_cost for s in outcome.summaries}
    _, doubled = rerender(spec.run_id, store=store, cfg=ctx.cfg, prices=scale_prices(ctx.prices, 2))
    checks.append(Check("--reprice applies new prices", all(s.total_cost == base[s.model] * 2 for s in doubled),
                        ", ".join(f"{s.model}: {format_usd(base[s.model])} -> {format_usd(s.total_cost)}" for s in doubled)))
    rerender(spec.run_id, store=store, cfg=ctx.cfg)  # restore the snapshot-priced report

    actual = sum((s.total_cost or Decimal(0) for s in outcome.summaries), Decimal(0))
    actual += evaluation_cost(rows, ctx.prices, ctx.cfg.judge) or Decimal(0)
    checks.append(Check("pre-run estimate within ±50% of actual", within(float(estimate), float(actual), 0.5),
                        f"estimate {format_usd(estimate)} vs actual {format_usd(actual)}"))
    return checks


@gate("g8", "UI")
def g8(ctx: GateContext) -> list[Check]:
    checks = [command_check("UI page tests (AppTest, offline)", ctx.run(["uv", "run", "pytest", "-q", "tests/ui"]))]
    live = build_context(ctx.profile)
    agent = next(iter(ctx.cfg.agents))
    result = live.client.invoke(agent, PONG)
    ui_cost = result_metrics(result, live.prices)["cost"]
    checks.append(Check("UI and CLI show the same cost for one call", f"cost={ui_cost}" in format_result(result, live.prices), f"ui={ui_cost}"))

    run_id = ctx.state.get("g7_run_id")
    for page, check in (("2_Benchmarks.py", "Benchmarks page lists the G7 run"), ("3_Costs.py", "Costs page renders")):
        at = AppTest.from_file(str(ctx.repo_root / "ui" / "pages" / page), default_timeout=120)
        at.session_state[OVERRIDE_KEY] = live
        at.run()
        if page == "2_Benchmarks.py":
            options = [o for sb in at.selectbox for o in sb.options]
            checks.append(Check(check, not at.exception and run_id in options, f"run={run_id} exception={at.exception}"))
        else:
            checks.append(Check(check, not at.exception, str(at.exception or "ok")))
    return checks
