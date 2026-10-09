"""Benchmark summaries and the self-contained HTML report (spec §5 Report contents)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean
from typing import Any

from jinja2 import Environment

from agentkit.metering import Usage
from bench.judge import bias_warnings
from bench.records import Row
from template_core.config import DeployConfig
from template_core.pricing import PriceTable, format_usd

PASS_SCORE = 4


@dataclass(frozen=True)
class ModelSummary:
    model: str
    calls: int
    errors: int
    scored: int
    total_cost: Decimal | None
    avg_cost: Decimal | None
    cost_per_1k: Decimal | None
    avg_input: float
    avg_output: float
    p50_ms: int | None
    p95_ms: int | None
    avg_score: float | None
    pass_rate: float | None
    error_rate: float
    agentcore_usd: Decimal | None


def percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def summarize(rows: list[Row], prices: PriceTable, agentcore_by_model: dict[str, Decimal] | None = None) -> list[ModelSummary]:
    by_model: dict[str, list[Row]] = {}
    for r in rows:
        by_model.setdefault(r.model, []).append(r)
    out = []
    for model, rs in by_model.items():
        usages = [Usage.from_dict(r.usage) for r in rs if r.usage]
        costs = [prices.try_model_cost(model, u) for u in usages]
        total = None if any(c is None for c in costs) else sum((c.total for c in costs), Decimal(0))
        avg = total / len(rs) if total is not None else None
        latencies = [Usage.from_dict(r.usage).latency_ms for r in rs if r.usage and r.error is None]
        scores = [r.score for r in rs if r.score is not None]
        errors = sum(1 for r in rs if r.error)
        out.append(
            ModelSummary(
                model=model,
                calls=len(rs),
                errors=errors,
                scored=len(scores),
                total_cost=total,
                avg_cost=avg,
                cost_per_1k=avg * 1000 if avg is not None else None,
                avg_input=mean(u.input_tokens for u in usages) if usages else 0.0,
                avg_output=mean(u.output_tokens for u in usages) if usages else 0.0,
                p50_ms=percentile(latencies, 50),
                p95_ms=percentile(latencies, 95),
                avg_score=mean(scores) if scores else None,
                pass_rate=sum(1 for s in scores if s >= PASS_SCORE) / len(scores) if scores else None,
                error_rate=errors / len(rs),
                agentcore_usd=(agentcore_by_model or {}).get(model),
            )
        )
    return out


def evaluation_cost(rows: list[Row], prices: PriceTable, judge_alias: str) -> Decimal | None:
    total = Decimal(0)
    for r in rows:
        if r.judge_usage:
            cost = prices.try_model_cost(judge_alias, Usage.from_dict({**r.judge_usage, "model": judge_alias}))
            if cost is None:
                return None
            total += cost.total
    return total


def pareto_front(summaries: list[ModelSummary]) -> set[str]:
    pts = [s for s in summaries if s.avg_cost is not None and s.avg_score is not None]
    front = set()
    for s in pts:
        dominated = any(
            o is not s and o.avg_cost <= s.avg_cost and o.avg_score >= s.avg_score and (o.avg_cost < s.avg_cost or o.avg_score > s.avg_score)
            for o in pts
        )
        if not dominated:
            front.add(s.model)
    return front


def allocate_by_latency(total: Decimal | None, rows: list[Row]) -> dict[str, Decimal]:
    if total is None:
        return {}
    weights: dict[str, int] = {}
    for r in rows:
        if r.usage:
            weights[r.model] = weights.get(r.model, 0) + Usage.from_dict(r.usage).latency_ms
    whole = sum(weights.values())
    return {m: total * w / whole for m, w in weights.items()} if whole else {}


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.0f}%"


def summary_table(summaries: list[ModelSummary]) -> list[dict[str, str]]:
    return [
        {
            "model": s.model,
            "calls": str(s.calls),
            "avg $/case": format_usd(s.avg_cost),
            "$ per 1k conversations": format_usd(s.cost_per_1k),
            "avg in/out tokens": f"{s.avg_input:.0f} / {s.avg_output:.0f}",
            "p50 / p95 ms": f"{s.p50_ms or '-'} / {s.p95_ms or '-'}",
            "avg score": "-" if s.avg_score is None else f"{s.avg_score:.2f}",
            "pass rate": _pct(s.pass_rate),
            "error rate": _pct(s.error_rate),
            "AgentCore (est.)": format_usd(s.agentcore_usd) if s.agentcore_usd is not None else "pending",
        }
        for s in summaries
    ]


def _scatter(summaries: list[ModelSummary], front: set[str], width: int = 640, height: int = 360, pad: int = 56) -> dict[str, Any]:
    pts = [s for s in summaries if s.avg_cost is not None and s.avg_score is not None]
    max_cost = max((float(s.avg_cost) for s in pts), default=0.0) or 1.0
    x = lambda c: pad + (width - 2 * pad) * float(c) / (max_cost * 1.1)  # noqa: E731
    y = lambda sc: height - pad - (height - 2 * pad) * (sc - 1) / 4  # noqa: E731
    return {
        "width": width, "height": height, "pad": pad, "max_label": format_usd(Decimal(str(max_cost * 1.1))),
        "points": [{"x": x(s.avg_cost), "y": y(s.avg_score), "label": s.model, "front": s.model in front} for s in pts],
        "grid": [{"y": y(v), "label": str(v)} for v in (1, 2, 3, 4, 5)],
    }


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Benchmark {{ meta.run_id }}</title>
<style>
:root { --bg:#fff; --fg:#1d1d1f; --muted:#6b6b70; --line:#e3e3e8; --accent:#2f6fde; --warn:#a15c00; --point:#9aa0aa; }
@media (prefers-color-scheme: dark) { :root { --bg:#141416; --fg:#ececf0; --muted:#9a9aa2; --line:#2c2c31; --accent:#7aa7ff; --warn:#f0b35a; --point:#6f7480; } }
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0 auto; max-width:1100px; padding:24px 16px; }
h1 { font-size:22px; margin:0 0 4px } h2 { font-size:17px; margin:28px 0 8px } .muted { color:var(--muted) }
table { border-collapse:collapse; width:100%; font-size:13px } th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top }
th { color:var(--muted); font-weight:600 } td.num { text-align:right; font-variant-numeric:tabular-nums }
.warn { color:var(--warn) } .front { font-weight:700 } .wrap { overflow-x:auto }
details summary { cursor:pointer } pre { white-space:pre-wrap; margin:4px 0 }
svg text { fill:var(--muted); font-size:11px } svg .lbl { fill:var(--fg); font-size:12px }
</style></head><body>
<h1>Benchmark {{ meta.run_id }}</h1>
<div class="muted">agent <b>{{ meta.agent }}</b> · suite <b>{{ meta.suite }}</b> · {{ meta.models|length }} models × {{ meta.repeats }} repeats · judge {{ meta.judge or "none" }} · {{ meta.created_at }}{% if repriced %} · <b>re-priced with current pricing.yaml</b>{% endif %}</div>
<p>Estimated before run: {{ meta.estimate_usd_fmt }} · actual model cost: {{ actual_fmt }} · evaluation (judge) cost: {{ eval_fmt }} · AgentCore compute (est.): {{ agentcore_fmt }}</p>
{% if warnings %}<h2>Warnings</h2><ul>{% for w in warnings %}<li class="warn">{{ w }}</li>{% endfor %}</ul>{% endif %}
<h2>Per model</h2><div class="wrap"><table><tr>{% for k in table[0].keys() %}<th>{{ k }}</th>{% endfor %}</tr>
{% for r in table %}<tr>{% for k, v in r.items() %}<td class="{{ 'front' if k == 'model' and v in front else '' }}">{{ v }}</td>{% endfor %}</tr>{% endfor %}</table></div>
<p class="muted">Bold models are on the cost/quality frontier: no other model is both cheaper and better.</p>
<h2>Cost vs quality</h2>
<svg viewBox="0 0 {{ sc.width }} {{ sc.height }}" width="100%" role="img" aria-label="Average cost per case against average judge score">
{% for g in sc.grid %}<line x1="{{ sc.pad }}" x2="{{ sc.width - sc.pad }}" y1="{{ g.y }}" y2="{{ g.y }}" stroke="var(--line)"/><text x="{{ sc.pad - 10 }}" y="{{ g.y + 4 }}" text-anchor="end">{{ g.label }}</text>{% endfor %}
<text x="{{ sc.pad }}" y="{{ sc.height - sc.pad + 18 }}">$0</text><text x="{{ sc.width - sc.pad }}" y="{{ sc.height - sc.pad + 18 }}" text-anchor="end">{{ sc.max_label }} per case</text>
{% for p in sc.points %}<circle cx="{{ p.x }}" cy="{{ p.y }}" r="6" fill="{{ 'var(--accent)' if p.front else 'var(--point)' }}"/><text class="lbl" x="{{ p.x + 9 }}" y="{{ p.y + 4 }}">{{ p.label }}</text>{% endfor %}
</svg>
<h2>Cases</h2>
{% for case_id, case_rows in cases.items() %}<h3>{{ case_id }}</h3><div class="wrap"><table><tr><th>model</th><th>#</th><th>score</th><th>cost</th><th>answer</th><th>judge reason / error</th></tr>
{% for r in case_rows %}<tr><td>{{ r.model }}</td><td>{{ r.repeat }}</td><td class="num">{{ r.score if r.score is not none else "-" }}</td><td class="num">{{ r.cost }}</td>
<td><details><summary>{{ (r.output or "")[:80] }}{% if (r.output or "")|length > 80 %}…{% endif %}</summary><pre>{{ r.output or "" }}</pre></details></td>
<td>{% if r.error %}<span class="warn">{{ r.error.type }}: {{ r.error.message }}</span>{% else %}{{ r.reason or r.judge_error or "" }}{% endif %}</td></tr>{% endfor %}
</table></div>{% endfor %}
</body></html>"""

_ENV = Environment(autoescape=True)


def render_html(
    meta: dict[str, Any],
    rows: list[Row],
    summaries: list[ModelSummary],
    *,
    eval_cost: Decimal | None,
    warnings: list[str],
    front: set[str],
    agentcore_usd: Decimal | None,
    repriced: bool,
    prices: PriceTable,
) -> str:
    cases: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        usage = Usage.from_dict(r.usage) if r.usage else None
        cost = prices.try_model_cost(r.model, usage) if usage else None
        cases.setdefault(r.case_id, []).append({**r.to_dict(), "cost": format_usd(cost.total) if cost else ("-" if usage is None else "unpriced")})
    totals = [s.total_cost for s in summaries]
    actual = None if any(t is None for t in totals) else sum(totals, Decimal(0))
    estimate = meta.get("estimate_usd")
    return _ENV.from_string(_TEMPLATE).render(
        meta={**meta, "estimate_usd_fmt": format_usd(Decimal(estimate)) if estimate else "-"},
        table=summary_table(summaries),
        front=front,
        sc=_scatter(summaries, front),
        cases=cases,
        warnings=warnings,
        repriced=repriced,
        actual_fmt=format_usd(actual),
        eval_fmt=format_usd(eval_cost) if meta.get("judge") else "none",
        agentcore_fmt=format_usd(agentcore_usd) if agentcore_usd is not None else "pending (metrics arrive within ~60 min; run `report --reprice`)",
    )


def build_report(
    meta: dict[str, Any],
    rows: list[Row],
    prices: PriceTable,
    cfg: DeployConfig,
    agentcore_usd: Decimal | None,
    repriced: bool = False,
) -> tuple[str, list[ModelSummary]]:
    summaries = summarize(rows, prices, allocate_by_latency(agentcore_usd, rows))
    judge = meta.get("judge")
    warnings = bias_warnings(cfg, judge, list(meta.get("models", []))) if judge else []
    warnings += [f"model '{m}' has no price in pricing.yaml" for m in meta.get("models", []) if m not in prices.models]
    if agentcore_usd is None:
        warnings.append("AgentCore compute cost is pending: runtime metrics can take up to 60 minutes to arrive.")
    eval_cost = evaluation_cost(rows, prices, judge) if judge else None
    html = render_html(
        meta, rows, summaries, eval_cost=eval_cost, warnings=warnings, front=pareto_front(summaries),
        agentcore_usd=agentcore_usd, repriced=repriced, prices=prices,
    )
    return html, summaries
