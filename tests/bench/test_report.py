from datetime import date
from decimal import Decimal

from bench.records import Row
from bench.report import (
    ModelSummary,
    allocate_by_latency,
    build_report,
    evaluation_cost,
    pareto_front,
    percentile,
    summarize,
)
from template_core.config import parse_config
from template_core.pricing import PriceTable

PRICES = PriceTable.model_validate(
    {"as_of": date(2026, 10, 9), "agentcore": {"vcpu_hour": 1, "gb_hour": 1},
     "models": {"m1": {"input": 1.0, "output": 5.0}, "m2": {"input": 10.0, "output": 50.0}, "j": {"input": 3.0, "output": 15.0}}}
)
CFG = parse_config(
    """
prefix: t1
region: eu-central-1
models:
  m1: { profile: eu.amazon.nova-pro-v1:0 }
  m2: { profile: eu.anthropic.claude-sonnet-4-6 }
  j: { profile: eu.anthropic.claude-haiku-4-5-20251001-v1:0 }
judge: j
agents:
  a: { models: [m1, m2], default: m1 }
"""
)


def row(model, case, score, inp=1000, out=100, latency=1000, error=None, output="ok", judge=True):
    usage = {"model": model, "input_tokens": inp, "output_tokens": out, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "latency_ms": latency, "cycles": 1, "tool_calls": 0}
    return Row("r1", "a", model, case, 1, "s", output, usage, error, latency + 50, 1, score, "why",
               {"model": "j", "input_tokens": 1000, "output_tokens": 100} if judge else None, None)


ROWS = [
    row("m1", "c1", 4, latency=1000), row("m1", "c2", 2, latency=3000),
    row("m2", "c1", 5, latency=2000), row("m2", "c2", None, error={"type": "X", "message": "boom"}, output=None, judge=False),
]


def test_percentile_nearest_rank():
    assert percentile([], 50) is None
    assert percentile([100, 200, 300, 400], 50) == 200
    assert percentile([100, 200, 300, 400], 95) == 400


def test_summarize_golden():
    s = {x.model: x for x in summarize(ROWS, PRICES)}
    m1 = s["m1"]
    # per call: 1000*1/1e6 + 100*5/1e6 = 0.0015
    assert m1.total_cost == Decimal("0.003") and m1.avg_cost == Decimal("0.0015") and m1.cost_per_1k == Decimal("1.5")
    assert m1.avg_score == 3.0 and m1.pass_rate == 0.5 and m1.error_rate == 0 and m1.p50_ms == 1000
    m2 = s["m2"]
    assert m2.errors == 1 and m2.error_rate == 0.5 and m2.scored == 1
    assert m2.total_cost == Decimal("0.03")  # failed call's partial usage still costs (spec §3)


def test_unpriced_model_does_not_crash():
    rows = [row("zz", "c1", 3)]
    s = summarize(rows, PRICES)[0]
    assert s.total_cost is None and s.avg_cost is None


def test_evaluation_cost():
    assert evaluation_cost(ROWS, PRICES, "j") == Decimal("0.0045") * 3


def test_pareto_front():
    a = ModelSummary("cheap-ok", 1, 0, 1, None, Decimal("1"), None, 0, 0, None, None, 3.0, None, 0, None)
    b = ModelSummary("pricey-best", 1, 0, 1, None, Decimal("5"), None, 0, 0, None, None, 5.0, None, 0, None)
    c = ModelSummary("dominated", 1, 0, 1, None, Decimal("6"), None, 0, 0, None, None, 4.0, None, 0, None)
    assert pareto_front([a, b, c]) == {"cheap-ok", "pricey-best"}


def test_allocate_by_latency():
    # latency weights: m1 = 1000 + 3000, m2 = 2000 + 1000 (the failed call still ran)
    alloc = allocate_by_latency(Decimal("1.00"), ROWS)
    assert alloc["m1"] == Decimal("1.00") * 4000 / 7000
    assert abs(sum(alloc.values()) - Decimal("1.00")) < Decimal("1e-20")
    assert allocate_by_latency(None, ROWS) == {}


def test_report_escapes_model_output_and_shows_warnings():  # Review Focus 3
    rows = [*ROWS, row("m1", "c3", 1, output="<script>alert(1)</script> æøå")]
    meta = {"run_id": "r1", "agent": "a", "suite": "s", "models": ["m1", "m2"], "repeats": 1, "judge": "j",
            "created_at": "2026-10-09T12:00:00Z", "estimate_usd": "0.01"}
    html, summaries = build_report(meta, rows, PRICES, CFG, agentcore_usd=None)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html and "æøå" in html
    assert "both anthropic models" in html
    assert "pending" in html.lower()
    assert {s.model for s in summaries} == {"m1", "m2"}
