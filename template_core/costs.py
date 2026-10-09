"""Cost views (spec §4): live estimate from EMF metrics × prices, AgentCore compute from runtime
metrics, billed cost from Cost Explorer by tag, and reconciliation between them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from botocore.exceptions import ClientError

from agentkit.metering import NAMESPACE, Usage
from template_core.config import DeployConfig
from template_core.pricing import PriceTable

AGENTCORE_NAMESPACE = "AWS/Bedrock-AgentCore"
TOKEN_METRICS = {
    "InputTokens": "input_tokens",
    "OutputTokens": "output_tokens",
    "CacheReadTokens": "cache_read_tokens",
    "CacheWriteTokens": "cache_write_tokens",
}
_DENIED = {"AccessDeniedException", "AccessDenied", "UnauthorizedOperation"}


def _dims(**kv: str) -> list[dict[str, str]]:
    return [{"Name": k, "Value": v} for k, v in kv.items()]


def _period(start: datetime, end: datetime) -> int:
    return 60 if end - start <= timedelta(hours=3) else 3600


def sum_metrics(cw, queries: dict[str, dict[str, Any]], start: datetime, end: datetime) -> dict[str, float | None]:
    """queries: id -> {"Namespace", "MetricName", "Dimensions"}. Returns id -> Sum over the window, None if no data."""
    period = _period(start, end)
    results: dict[str, float | None] = dict.fromkeys(queries)
    ids = list(queries)
    for i in range(0, len(ids), 500):
        mdq = [
            {"Id": qid, "MetricStat": {"Metric": queries[qid], "Period": period, "Stat": "Sum"}, "ReturnData": True}
            for qid in ids[i : i + 500]
        ]
        token = None
        while True:
            kwargs: dict[str, Any] = {"MetricDataQueries": mdq, "StartTime": start, "EndTime": end}
            if token:
                kwargs["NextToken"] = token
            resp = cw.get_metric_data(**kwargs)
            for r in resp["MetricDataResults"]:
                if r.get("Values"):
                    results[r["Id"]] = (results[r["Id"]] or 0.0) + float(sum(r["Values"]))
            token = resp.get("NextToken")
            if not token:
                break
    return results


@dataclass(frozen=True)
class LiveCost:
    agent: str
    model: str
    usage: Usage
    errors: int
    estimate_usd: Decimal | None


def live_costs(cw, cfg: DeployConfig, prices: PriceTable, start: datetime, end: datetime) -> list[LiveCost]:
    queries: dict[str, dict[str, Any]] = {}
    index: dict[str, tuple[str, str, str]] = {}
    for agent, a in cfg.agents.items():
        for model in a.models:
            for metric in [*TOKEN_METRICS, "Errors"]:
                qid = f"q{len(queries)}"
                index[qid] = (agent, model, metric)
                queries[qid] = {"Namespace": NAMESPACE, "MetricName": metric, "Dimensions": _dims(Agent=agent, Model=model)}
    sums = sum_metrics(cw, queries, start, end)
    totals: dict[tuple[str, str], dict[str, int]] = {}
    for qid, (agent, model, metric) in index.items():
        totals.setdefault((agent, model), {})[metric] = int(sums[qid] or 0)
    rows = []
    for (agent, model), t in totals.items():
        usage = Usage(model, **{field: t.get(metric, 0) for metric, field in TOKEN_METRICS.items()})
        cost = prices.try_model_cost(model, usage)
        rows.append(LiveCost(agent, model, usage, t.get("Errors", 0), cost.total if cost else None))
    return rows


def agentcore_usage(cw, runtime_arn: str, start: datetime, end: datetime) -> tuple[float, float] | None:
    dims = _dims(Service="AgentCore.Runtime", Resource=runtime_arn)
    sums = sum_metrics(
        cw,
        {
            "cpu": {"Namespace": AGENTCORE_NAMESPACE, "MetricName": "CPUUsed-vCPUHours", "Dimensions": dims},
            "mem": {"Namespace": AGENTCORE_NAMESPACE, "MetricName": "MemoryUsed-GBHours", "Dimensions": dims},
        },
        start,
        end,
    )
    if sums["cpu"] is None and sums["mem"] is None:
        return None
    return sums["cpu"] or 0.0, sums["mem"] or 0.0


def agentcore_window_cost(cw, runtime_arn: str, start: datetime, end: datetime, prices: PriceTable) -> Decimal | None:
    usage = agentcore_usage(cw, runtime_arn, start, end)
    return None if usage is None else prices.agentcore_cost(*usage)


BilledStatus = Literal["ok", "no_tagged_costs", "tags_inactive", "no_access"]


@dataclass(frozen=True)
class BilledCosts:
    status: BilledStatus
    message: str
    by_pair: dict[tuple[str, str], Decimal]
    agent_compute: dict[str, Decimal]  # costs tagged with agent but no model (AgentCore runtime compute)
    judge: Decimal  # tagged with model but no agent (judge profile)
    untagged: Decimal


def cost_tag_state(ce, keys: list[str]) -> str:
    try:
        resp = ce.list_cost_allocation_tags(TagKeys=list(keys))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _DENIED:
            return "unknown"
        raise
    active = {t["TagKey"] for t in resp.get("CostAllocationTags", []) if t.get("Status") == "Active"}
    missing = [k for k in keys if k not in active]
    return "active" if not missing else "inactive:" + ",".join(missing)


def billed_costs(ce, cfg: DeployConfig, start: date, end: date) -> BilledCosts:
    tag_state = cost_tag_state(ce, ["project", "agent", "model"])
    by_pair: dict[tuple[str, str], Decimal] = {}
    compute: dict[str, Decimal] = {}
    judge = untagged = Decimal(0)
    token = None
    try:
        while True:
            kwargs: dict[str, Any] = {
                "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"],
                "Filter": {"Tags": {"Key": "project", "Values": [cfg.prefix], "MatchOptions": ["EQUALS"]}},
                "GroupBy": [{"Type": "TAG", "Key": "agent"}, {"Type": "TAG", "Key": "model"}],
            }
            if token:
                kwargs["NextPageToken"] = token
            resp = ce.get_cost_and_usage(**kwargs)
            for period in resp.get("ResultsByTime", []):
                for group in period.get("Groups", []):
                    agent = group["Keys"][0].split("$", 1)[1]
                    model = group["Keys"][1].split("$", 1)[1]
                    amount = Decimal(group["Metrics"]["UnblendedCost"]["Amount"])
                    if agent and model:
                        by_pair[(agent, model)] = by_pair.get((agent, model), Decimal(0)) + amount
                    elif agent:
                        compute[agent] = compute.get(agent, Decimal(0)) + amount
                    elif model:
                        judge += amount
                    else:
                        untagged += amount
            token = resp.get("NextPageToken")
            if not token:
                break
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in _DENIED:
            return BilledCosts("no_access", f"Cost Explorer is not readable with these credentials ({code}).", {}, {}, Decimal(0), Decimal(0))
        raise

    if by_pair or compute or judge:
        return BilledCosts("ok", "Billed cost by tag (Cost Explorer, daily granularity).", by_pair, compute, judge, untagged)
    if tag_state.startswith("inactive"):
        missing = tag_state.split(":", 1)[1]
        message = (
            f"Cost allocation tags not active: {missing}. Activate them in Billing > Cost allocation tags "
            "(in the payer account for AWS Organizations); data appears about 24 hours later."
        )
        return BilledCosts("tags_inactive", message, {}, {}, Decimal(0), untagged)
    message = (
        "No costs carry the project tag yet: the cost allocation tags may not be activated (in the payer account "
        "if this account is in an AWS Organization), or data is not available yet (about 24 hours after activation and usage)."
    )
    if tag_state == "unknown":
        message += " These credentials cannot read tag activation status (linked account)."
    return BilledCosts("no_tagged_costs", message, {}, {}, Decimal(0), untagged)


@dataclass(frozen=True)
class ReconRow:
    agent: str
    model: str
    estimate: Decimal | None
    billed: Decimal | None
    delta_pct: float | None
    within: bool | None  # None = not comparable (see note)
    note: str


def reconcile(live: list[LiveCost], billed: BilledCosts, cfg: DeployConfig, tolerance: float = 0.10) -> list[ReconRow]:
    rows = []
    for item in live:
        amount = billed.by_pair.get((item.agent, item.model))
        if not cfg.models[item.model].billing_tag:
            rows.append(ReconRow(item.agent, item.model, item.estimate_usd, amount, None, None, "no billing tag (application profile unsupported)"))
        elif item.estimate_usd is None:
            rows.append(ReconRow(item.agent, item.model, None, amount, None, None, "unpriced in pricing.yaml"))
        elif not amount:
            note = "no usage" if item.estimate_usd == 0 else "no billed data yet"
            rows.append(ReconRow(item.agent, item.model, item.estimate_usd, amount, None, None, note))
        elif item.estimate_usd == 0:
            rows.append(ReconRow(item.agent, item.model, item.estimate_usd, amount, None, None, "billed cost without metered usage"))
        else:
            delta = float((amount - item.estimate_usd) / item.estimate_usd * 100)
            rows.append(ReconRow(item.agent, item.model, item.estimate_usd, amount, delta, abs(delta) <= tolerance * 100, ""))
    return rows
