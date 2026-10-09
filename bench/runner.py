"""Runs a suite against a deployed agent across models and repeats (spec §5 Execution)."""

from __future__ import annotations

import random
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from agentkit.metering import Usage
from bench.records import Row, Task, Verdict
from bench.suite import Case, Suite
from template_core.agentcore import InvokeResult
from template_core.pricing import PriceTable

JUDGE_ESTIMATE = Usage(model="judge", input_tokens=300, output_tokens=60)

InvokeFn = Callable[[Task, str], InvokeResult]
GradeFn = Callable[[Case, str], Verdict]


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    agent: str
    suite: Suite
    models: list[str]
    repeats: int
    concurrency: int = 4
    max_attempts: int = 4

    def tasks(self) -> list[Task]:
        return [Task(m, c, r) for m in self.models for c in self.suite.cases for r in range(1, self.repeats + 1)]


def new_run_id(agent: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{agent}-{stamp}-{secrets.token_hex(2)}"


def estimate_cost(spec: RunSpec, prices: PriceTable, judge_alias: str | None) -> Decimal:
    per_call = Usage("", input_tokens=spec.suite.estimate.input_tokens, output_tokens=spec.suite.estimate.output_tokens)
    calls_per_model = len(spec.suite.cases) * spec.repeats
    total = sum((prices.model_cost(m, per_call).total * calls_per_model for m in spec.models), Decimal(0))
    if judge_alias:
        total += prices.model_cost(judge_alias, JUDGE_ESTIMATE).total * len(spec.tasks())
    return total


def _merge_usage(billed: list[Usage], final: Usage | None) -> Usage:
    """Token counts summed over every attempt; latency, cycles and tool calls from the final attempt only."""
    total = billed[0]
    for usage in billed[1:]:
        total = total + usage
    model = final.model if final is not None else billed[-1].model
    last = final or Usage(model=model)
    return replace(
        total, model=model, latency_ms=last.latency_ms, cycles=last.cycles, tool_calls=last.tool_calls,
    )


def _backoff(attempt: int) -> float:
    return min(30.0, 2.0**attempt) + random.uniform(0, 1)


def run_benchmark(
    spec: RunSpec,
    invoke: InvokeFn,
    grade: GradeFn | None = None,
    on_row: Callable[[Row], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Row]:
    def one(task: Task) -> Row:
        attempts = 0
        billed: list[Usage] = []
        try:
            while True:
                attempts += 1
                result = invoke(task, spec.run_id)
                if result.usage is not None:
                    billed.append(result.usage)
                if result.throttled and attempts < spec.max_attempts:
                    sleep(_backoff(attempts))
                    continue
                break
            row = Row(
                run_id=spec.run_id, agent=spec.agent, model=task.model, case_id=task.case.id, repeat=task.repeat,
                session_id=result.session_id, output=result.output,
                usage=_merge_usage(billed, result.usage).to_dict() if billed else None, error=result.error,
                client_latency_ms=result.client_latency_ms, attempts=attempts,
            )
            if grade is not None and result.ok:
                try:
                    verdict = grade(task.case, result.output)
                except Exception as exc:  # noqa: BLE001 - a judge failure must not discard the invoke data
                    row.judge_error = f"{type(exc).__name__}: {exc}"
                else:
                    row.score, row.reason, row.judge_error = verdict.score, verdict.reason, verdict.error
                    row.judge_usage = verdict.usage.to_dict() if verdict.usage else None
        except Exception as exc:  # noqa: BLE001 - failures are recorded as rows, never abort the run (spec §5)
            row = Row(
                run_id=spec.run_id, agent=spec.agent, model=task.model, case_id=task.case.id, repeat=task.repeat,
                session_id=None, output=None, usage=None, error={"type": type(exc).__name__, "message": str(exc)},
                client_latency_ms=0, attempts=attempts,
            )
        if on_row is not None:
            on_row(row)
        return row

    with ThreadPoolExecutor(max_workers=max(1, spec.concurrency)) as pool:
        futures = [pool.submit(one, task) for task in spec.tasks()]
        return [f.result() for f in futures]
