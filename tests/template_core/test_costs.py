from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from botocore.exceptions import ClientError

from template_core.costs import (
    BilledCosts,
    LiveCost,
    agentcore_window_cost,
    billed_costs,
    completed_days_window,
    cost_tag_state,
    emf_dimensions,
    live_costs,
    reconcile,
)
from template_core.config import parse_config
from template_core.pricing import PriceTable
from agentkit.metering import Usage

CFG = parse_config(
    """
prefix: t1
region: eu-central-1
models:
  m1: { profile: eu.anthropic.claude-haiku-4-5-20251001-v1:0 }
  k3: { profile: global.moonshotai.kimi-k3, billing_tag: false }
judge: m1
agents:
  a: { models: [m1, k3], default: m1 }
"""
)
PRICES = PriceTable.model_validate(
    {"as_of": date(2026, 10, 9), "agentcore": {"vcpu_hour": 0.0895, "gb_hour": 0.00945},
     "models": {"m1": {"input": 1.0, "output": 5.0}, "k3": {"input": 0.5, "output": 2.0}}}
)
START = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
END = START + timedelta(hours=1)


class FakeCW:
    def __init__(self, data):
        self.data = data  # (namespace, metric, frozenset(dims.items())) -> list of values

    def get_metric_data(self, MetricDataQueries, StartTime, EndTime, NextToken=None):
        results = []
        for q in MetricDataQueries:
            m = q["MetricStat"]["Metric"]
            key = (m["Namespace"], m["MetricName"], frozenset((d["Name"], d["Value"]) for d in m["Dimensions"]))
            results.append({"Id": q["Id"], "Values": self.data.get(key, [])})
        return {"MetricDataResults": results}


def emf(metric, model, values, project="t1"):
    return ("AgentTemplate", metric, frozenset({("Project", project), ("Agent", "a"), ("Model", model)})), values


def test_emf_dimensions_include_project():
    assert emf_dimensions("t1", "a", "m1") == [
        {"Name": "Project", "Value": "t1"}, {"Name": "Agent", "Value": "a"}, {"Name": "Model", "Value": "m1"},
    ]


def test_live_costs_ignore_other_deployments_series():  # a second copy (tmpl2) writes the same Agent/Model
    cw = FakeCW(dict([emf("InputTokens", "m1", [1_000_000], project="t2")]))
    rows = {(r.agent, r.model): r for r in live_costs(cw, CFG, PRICES, START, END)}
    assert rows[("a", "m1")].usage.input_tokens == 0


def test_live_costs_sum_tokens_and_price_them():
    cw = FakeCW(dict([emf("InputTokens", "m1", [600_000, 400_000]), emf("OutputTokens", "m1", [100_000]), emf("Errors", "m1", [2])]))
    rows = {(r.agent, r.model): r for r in live_costs(cw, CFG, PRICES, START, END)}
    m1 = rows[("a", "m1")]
    assert m1.usage.input_tokens == 1_000_000 and m1.errors == 2
    assert m1.estimate_usd == Decimal("1.5")
    assert rows[("a", "k3")].usage.input_tokens == 0


def test_agentcore_cost_from_runtime_metrics_or_none():
    dims = frozenset({("Service", "AgentCore.Runtime"), ("Resource", "arn:rt")})
    cw = FakeCW({("AWS/Bedrock-AgentCore", "CPUUsed-vCPUHours", dims): [2.0], ("AWS/Bedrock-AgentCore", "MemoryUsed-GBHours", dims): [10.0]})
    assert agentcore_window_cost(cw, "arn:rt", START, END, PRICES) == Decimal("0.0895") * 2 + Decimal("0.00945") * 10
    assert agentcore_window_cost(FakeCW({}), "arn:rt", START, END, PRICES) is None


class FakeCE:
    def __init__(self, groups=None, tags=None, deny_tags=False, deny_costs=False):
        self.groups, self.tags, self.deny_tags, self.deny_costs = groups or [], tags or [], deny_tags, deny_costs

    def list_cost_allocation_tags(self, TagKeys):
        if self.deny_tags:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "linked account"}}, "ListCostAllocationTags")
        return {"CostAllocationTags": self.tags}

    def get_cost_and_usage(self, **kwargs):
        if self.deny_costs:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetCostAndUsage")
        assert kwargs["Filter"] == {"Tags": {"Key": "project", "Values": ["t1"], "MatchOptions": ["EQUALS"]}}
        return {"ResultsByTime": [{"Groups": [{"Keys": list(k), "Metrics": {"UnblendedCost": {"Amount": a}}} for k, a in self.groups]}]}


def test_billed_costs_split_pairs_from_judge_compute_and_untagged():  # Review Focus 5
    ce = FakeCE(
        groups=[
            (("agent$a", "model$m1"), "1.20"),
            (("agent$", "model$m1"), "0.30"),  # judge profile: model tag, no agent tag
            (("agent$a", "model$"), "0.05"),  # AgentCore runtime compute: agent tag only
            (("agent$", "model$"), "0.01"),
        ],
        deny_tags=True,
    )
    b = billed_costs(ce, CFG, date(2026, 10, 8), date(2026, 10, 10))
    assert b.status == "ok"
    assert b.by_pair == {("a", "m1"): Decimal("1.20")}
    assert b.judge == Decimal("0.30") and b.agent_compute == {"a": Decimal("0.05")} and b.untagged == Decimal("0.01")


def test_billed_costs_explain_missing_data():
    b = billed_costs(FakeCE(deny_tags=True), CFG, date(2026, 10, 8), date(2026, 10, 10))
    assert b.status == "no_tagged_costs" and "payer" in b.message and "linked account" in b.message
    inactive = billed_costs(FakeCE(tags=[{"TagKey": "project", "Status": "Inactive"}]), CFG, date(2026, 10, 8), date(2026, 10, 10))
    assert inactive.status == "tags_inactive" and "project" in inactive.message
    denied = billed_costs(FakeCE(deny_costs=True, deny_tags=True), CFG, date(2026, 10, 8), date(2026, 10, 10))
    assert denied.status == "no_access"


def test_cost_tag_state():
    assert cost_tag_state(FakeCE(deny_tags=True), ["project"]) == "unknown"
    active = [{"TagKey": k, "Status": "Active"} for k in ("project", "agent", "model")]
    assert cost_tag_state(FakeCE(tags=active), ["project", "agent", "model"]) == "active"
    assert cost_tag_state(FakeCE(tags=active[:1]), ["project", "agent"]) == "inactive:agent"


def test_reconcile_tolerance_and_notes():
    live = [
        LiveCost("a", "m1", Usage("m1", 1), 0, Decimal("1.00")),
        LiveCost("a", "k3", Usage("k3", 1), 0, Decimal("0.50")),
    ]
    billed = BilledCosts("ok", "", {("a", "m1"): Decimal("1.05")}, {}, Decimal(0), Decimal(0))
    rows = {r.model: r for r in reconcile(live, billed, CFG)}
    assert rows["m1"].within is True and abs(rows["m1"].delta_pct - 5.0) < 1e-9
    assert rows["k3"].within is None and "no billing tag" in rows["k3"].note
    off = reconcile(live[:1], BilledCosts("ok", "", {("a", "m1"): Decimal("1.50")}, {}, Decimal(0), Decimal(0)), CFG)
    assert off[0].within is False
    none = reconcile(live[:1], BilledCosts("ok", "", {}, {}, Decimal(0), Decimal(0)), CFG)
    assert none[0].within is None and "no billed data" in none[0].note


def test_billed_costs_warn_when_tags_inactive_but_data_present():
    ce = FakeCE(groups=[(("agent$", "model$m1"), "0.30")], tags=[{"TagKey": "agent", "Status": "Inactive"}])
    b = billed_costs(ce, CFG, date(2026, 10, 8), date(2026, 10, 10))
    assert b.status == "ok" and b.judge == Decimal("0.30")
    assert "not active" in b.message


def test_reconcile_unavailable_status_marks_every_row():
    live = [LiveCost("a", "m1", Usage("m1", 1), 0, Decimal("1.00"))]
    rows = reconcile(live, BilledCosts("no_access", "denied", {}, {}, Decimal(0), Decimal(0)), CFG)
    assert rows[0].within is None and "no_access" in rows[0].note and rows[0].delta_pct is None


def test_reconcile_compares_genuine_zero_billed():
    live = [LiveCost("a", "m1", Usage("m1", 1), 0, Decimal("1.00"))]
    rows = reconcile(live, BilledCosts("ok", "", {("a", "m1"): Decimal(0)}, {}, Decimal(0), Decimal(0)), CFG)
    assert rows[0].within is False and abs(rows[0].delta_pct + 100.0) < 1e-9


def test_completed_days_window_excludes_today():
    start_day, end_day, start, end = completed_days_window(date(2026, 10, 9), 7)
    assert (start_day, end_day) == (date(2026, 10, 2), date(2026, 10, 9))
    assert (start, end) == (datetime(2026, 10, 2, tzinfo=UTC), datetime(2026, 10, 9, tzinfo=UTC))
