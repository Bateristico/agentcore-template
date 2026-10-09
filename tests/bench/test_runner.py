import threading
from datetime import UTC, date, datetime
from decimal import Decimal

from agentkit.metering import Usage
from bench.records import Row, Verdict
from bench.runner import RunSpec, estimate_cost, new_run_id, run_benchmark
from bench.suite import Case, Estimate, Suite
from template_core.agentcore import InvokeResult
from template_core.pricing import PriceTable

SUITE = Suite(suite="s", estimate=Estimate(input_tokens=1000, output_tokens=100), cases=[Case(id="c1", prompt="p1"), Case(id="c2", prompt="p2")])
PRICES = PriceTable.model_validate(
    {"as_of": date(2026, 10, 9), "agentcore": {"vcpu_hour": 1, "gb_hour": 1},
     "models": {"m1": {"input": 1.0, "output": 5.0}, "j": {"input": 3.0, "output": 15.0}}}
)


def ok(task, text="answer"):
    return InvokeResult("a", task.model, "s" * 40, text, Usage(task.model, 100, 10, latency_ms=200), None, 250)


def throttled(task):
    return InvokeResult("a", task.model, "s" * 40, None, Usage(task.model), {"type": "ModelThrottledException", "message": "slow"}, 50)


def spec(**kw):
    return RunSpec(run_id="r1", agent="a", suite=SUITE, models=["m1"], repeats=kw.pop("repeats", 2), **kw)


def test_tasks_cover_models_cases_repeats():
    s = RunSpec(run_id="r", agent="a", suite=SUITE, models=["m1", "m2"], repeats=3)
    assert len(s.tasks()) == 12 and {t.repeat for t in s.tasks()} == {1, 2, 3}


def test_estimate_cost_golden():
    # per call: 1000*1/1e6 + 100*5/1e6 = 0.0015; 4 calls = 0.006
    # judge per call: 1500*3/1e6 + 150*15/1e6 = 0.00675; 4 calls = 0.027
    assert estimate_cost(spec(), PRICES, None) == Decimal("0.006")
    assert estimate_cost(spec(), PRICES, "j") == Decimal("0.033")


def test_run_id_format():
    rid = new_run_id("tools-agent", datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC))
    assert rid.startswith("tools-agent-20261009T120000Z-") and len(rid) == len("tools-agent-20261009T120000Z-") + 4


def test_throttle_is_retried_with_backoff():
    calls, sleeps = [], []

    def invoke(task, run_id):
        calls.append(task)
        return throttled(task) if len(calls) == 1 else ok(task)

    rows = run_benchmark(spec(repeats=1, concurrency=1), invoke, sleep=sleeps.append)
    assert len(rows) == 2 and rows[0].attempts == 2 and rows[0].error is None
    assert len(sleeps) == 1 and sleeps[0] >= 2


def test_gives_up_after_max_attempts_and_records_the_error():
    rows = run_benchmark(spec(repeats=1, max_attempts=3), lambda t, r: throttled(t), sleep=lambda s: None)
    assert all(r.attempts == 3 and r.error["type"] == "ModelThrottledException" for r in rows)


def test_only_successful_calls_are_graded_and_empty_answers_are_graded():  # Review Focus 2
    graded = []

    def invoke(task, run_id):
        if task.case.id == "c2":
            return InvokeResult("a", task.model, "s" * 40, None, None, {"type": "RuntimeClientError", "message": "x"}, 5)
        return ok(task, text="")

    def grade(case, response):
        graded.append((case.id, response))
        return Verdict(1, "empty", Usage("j", 10, 2), None)

    rows = run_benchmark(spec(repeats=1), invoke, grade=grade)
    assert graded == [("c1", "")]
    by_case = {r.case_id: r for r in rows}
    assert by_case["c1"].score == 1 and by_case["c1"].judge_usage["input_tokens"] == 10
    assert by_case["c2"].score is None and by_case["c2"].error["type"] == "RuntimeClientError"


def test_unexpected_exception_becomes_an_error_row():
    def invoke(task, run_id):
        raise KeyError("boom")

    rows = run_benchmark(spec(repeats=1), invoke)
    assert all(r.error["type"] == "KeyError" for r in rows) and len(rows) == 2


def test_on_row_called_for_every_row_and_rows_round_trip():
    seen, lock = [], threading.Lock()

    def on_row(row):
        with lock:
            seen.append(row)

    rows = run_benchmark(spec(), lambda t, r: ok(t), on_row=on_row)
    assert len(seen) == 4
    assert Row.from_dict(rows[0].to_dict()) == rows[0]
