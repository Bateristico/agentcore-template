"""One benchmark run end to end: invoke, judge, price, report, store. Used by bench/run.py, the UI and validation."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bench.judge import Judge
from bench.records import Row
from bench.report import ModelSummary, build_report
from bench.runner import RunSpec, run_benchmark
from bench.store import RunStore
from template_core.agentcore import AgentClient
from template_core.config import REPO_ROOT, DeployConfig
from template_core.costs import agentcore_window_cost
from template_core.deployment import Deployment
from template_core.pricing import PriceTable


@dataclass(frozen=True)
class RunOutcome:
    meta: dict[str, Any]
    rows: list[Row]
    summaries: list[ModelSummary]
    html: str
    locations: list[str]


def git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def build_meta(spec: RunSpec, cfg: DeployConfig, prices: PriceTable, estimate: Decimal, started: datetime,
               finished: datetime, judge_alias: str | None, runtime_arn: str) -> dict[str, Any]:
    return {
        "run_id": spec.run_id,
        "agent": spec.agent,
        "suite": spec.suite.suite,
        "models": list(spec.models),
        "repeats": spec.repeats,
        "judge": judge_alias,
        "created_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "git_sha": git_sha(),
        "region": cfg.region,
        "runtime_arn": runtime_arn,
        "estimate_usd": str(estimate),
        "config": cfg.model_dump(mode="json"),
        "pricing": prices.model_dump(mode="json"),
    }


def _agentcore_cost(cloudwatch, meta: dict[str, Any], prices: PriceTable) -> Decimal | None:
    if cloudwatch is None:
        return None
    return agentcore_window_cost(
        cloudwatch, meta["runtime_arn"], datetime.fromisoformat(meta["started_at"]), datetime.fromisoformat(meta["finished_at"]), prices
    )


def execute_run(spec: RunSpec, *, cfg: DeployConfig, prices: PriceTable, deployment: Deployment, client: AgentClient,
                judge: Judge | None, store: RunStore, estimate: Decimal, cloudwatch: Any = None,
                on_row: Callable[[Row], None] | None = None) -> RunOutcome:
    started = datetime.now(UTC)
    rows = run_benchmark(
        spec,
        invoke=lambda task, run_id: client.invoke(spec.agent, task.case.prompt, model=task.model, run_id=run_id, case_id=task.case.id),
        grade=judge.grade if judge else None,
        on_row=on_row,
    )
    finished = datetime.now(UTC)
    meta = build_meta(spec, cfg, prices, estimate, started, finished, judge.alias if judge else None,
                      deployment.agents[spec.agent].runtime_arn)
    html, summaries = build_report(meta, rows, prices, cfg, _agentcore_cost(cloudwatch, meta, prices))
    return RunOutcome(meta, rows, summaries, html, store.save(meta, rows, html))


def rerender(run_id: str, *, store: RunStore, cfg: DeployConfig, prices: PriceTable | None = None,
             cloudwatch: Any = None) -> tuple[str, list[ModelSummary]]:
    meta, rows = store.load(run_id)
    table = prices if prices is not None else PriceTable.model_validate(meta["pricing"])
    html, summaries = build_report(meta, rows, table, cfg, _agentcore_cost(cloudwatch, meta, table), repriced=prices is not None)
    store.save_report(run_id, html)
    return html, summaries
