from datetime import UTC, datetime

import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError

from template_core.costs import agentcore_window_cost, billed_costs, completed_days_window, live_costs, reconcile
from template_core.pricing import format_usd
from ui.context import get_context

st.set_page_config(page_title="Costs", layout="wide")
ctx = get_context()
st.title("Costs")
days = st.slider("Window (whole completed UTC days, ending yesterday)", 1, 30, 7)
now = datetime.now(UTC)
# Reconciliation uses whole completed UTC days (end = today 00:00 UTC, exclusive), like scripts/reconcile_costs.py;
# the live estimate also covers today's partial day so far.
start_day, end_day, start, day_end = completed_days_window(now.date(), days)

try:
    live = live_costs(ctx.cloudwatch, ctx.cfg, ctx.prices, start, now)
    live_recon = live_costs(ctx.cloudwatch, ctx.cfg, ctx.prices, start, day_end)
except (ClientError, BotoCoreError) as exc:
    st.error(f"CloudWatch is not readable: {exc}")
    live = live_recon = None

st.subheader("Live estimate")
st.caption(f"CloudWatch token metrics × current pricing.yaml, {start_day} 00:00 UTC to now (includes today's partial day)")
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
            cost = agentcore_window_cost(ctx.cloudwatch, ep.runtime_arn, start, now, ctx.prices)
            text = format_usd(cost) if cost is not None else "no metrics yet"
        except (ClientError, BotoCoreError) as exc:
            text = f"unavailable: {exc}"
        compute.append({"agent": name, "estimate": text})
    st.dataframe(compute, use_container_width=True)

st.subheader("Billed (Cost Explorer, by tag)")
st.caption(f"Whole completed UTC days {start_day} to {end_day} (exclusive): today is not compared, its billing is incomplete.")
try:
    billed = billed_costs(ctx.cost_explorer, ctx.cfg, start_day, end_day)
except (ClientError, BotoCoreError) as exc:  # access denied is handled inside; e.g. ExpiredToken lands here
    billed = None
    st.warning(f"Cost Explorer is not readable: {exc}")
if billed is not None and billed.status != "ok":
    st.warning(billed.message)
elif billed is not None:
    st.caption(billed.message)
if live_recon is not None and billed is not None:
    st.dataframe(
        [{"agent": r.agent, "model": r.model, "estimate": format_usd(r.estimate), "billed": "-" if r.billed is None else format_usd(r.billed),
          "delta": "-" if r.delta_pct is None else f"{r.delta_pct:+.1f}%",
          "within ±10%": "-" if r.within is None else ("yes" if r.within else "no"), "note": r.note}
         for r in reconcile(live_recon, billed, ctx.cfg)],
        use_container_width=True,
    )
if billed is not None and billed.status == "ok":
    compute_text = ", ".join(f"{a} {format_usd(v)}" for a, v in billed.agent_compute.items()) or "none"
    st.caption(f"Judge (evaluation) cost: {format_usd(billed.judge)} · AgentCore compute billed: {compute_text}")
