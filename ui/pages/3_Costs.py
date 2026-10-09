from datetime import UTC, datetime, timedelta

import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError

from template_core.costs import agentcore_window_cost, billed_costs, live_costs, reconcile
from template_core.pricing import format_usd
from ui.context import get_context

st.set_page_config(page_title="Costs", layout="wide")
ctx = get_context()
st.title("Costs")
days = st.slider("Window (whole UTC days, ending today)", 1, 30, 7)
end_day = datetime.now(UTC).date() + timedelta(days=1)
start_day = end_day - timedelta(days=days)
start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
end = min(datetime.now(UTC), datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC))

try:
    live = live_costs(ctx.cloudwatch, ctx.cfg, ctx.prices, start, end)
except (ClientError, BotoCoreError) as exc:
    st.error(f"CloudWatch is not readable: {exc}")
    live = None

st.subheader("Live estimate")
st.caption("CloudWatch token metrics × current pricing.yaml")
if live is not None:
    st.dataframe(
        [{"agent": r.agent, "model": r.model, "tokens in": r.usage.input_tokens, "tokens out": r.usage.output_tokens,
          "errors": r.errors, "estimate": format_usd(r.estimate_usd)} for r in live],
        use_container_width=True,
    )

if ctx.deployment is not None:
    st.subheader("AgentCore compute (estimate)")
    compute = []
    for name, ep in ctx.deployment.agents.items():
        try:
            cost = agentcore_window_cost(ctx.cloudwatch, ep.runtime_arn, start, end, ctx.prices)
            text = format_usd(cost) if cost is not None else "no metrics yet"
        except (ClientError, BotoCoreError) as exc:
            text = f"unavailable: {exc}"
        compute.append({"agent": name, "estimate": text})
    st.dataframe(compute, use_container_width=True)

st.subheader("Billed (Cost Explorer, by tag)")
try:
    billed = billed_costs(ctx.cost_explorer, ctx.cfg, start_day, end_day)
except BotoCoreError as exc:
    billed = None
    st.warning(f"Cost Explorer is not readable: {exc}")
if billed is not None and billed.status != "ok":
    st.warning(billed.message)
elif billed is not None:
    st.caption(billed.message)
if live is not None and billed is not None:
    st.dataframe(
        [{"agent": r.agent, "model": r.model, "estimate": format_usd(r.estimate), "billed": "-" if r.billed is None else format_usd(r.billed),
          "delta": "-" if r.delta_pct is None else f"{r.delta_pct:+.1f}%",
          "within ±10%": "-" if r.within is None else ("yes" if r.within else "no"), "note": r.note}
         for r in reconcile(live, billed, ctx.cfg)],
        use_container_width=True,
    )
if billed is not None and billed.status == "ok":
    compute_text = ", ".join(f"{a} {format_usd(v)}" for a, v in billed.agent_compute.items()) or "none"
    st.caption(f"Judge (evaluation) cost: {format_usd(billed.judge)} · AgentCore compute billed: {compute_text}")
