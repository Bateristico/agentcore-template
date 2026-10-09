"""Home page. Run: uv run streamlit run ui/app.py -- --profile <profile>"""

import streamlit as st

from ui.context import get_context

st.set_page_config(page_title="Agent template", layout="wide")
ctx = get_context()
st.title("AgentCore + Strands template")
st.caption(f"prefix `{ctx.cfg.prefix}` · region `{ctx.cfg.region}` · profile `{ctx.profile or 'default'}` · prices as of {ctx.prices.as_of}")
if ctx.deployment is None:
    st.error(f"Agents are not deployed: {ctx.deployment_error}")
    st.markdown("Deploy with `npx cdk deploy --all --profile <profile>` in `infra/` (README §4).")
else:
    st.success(f"{len(ctx.deployment.agents)} agents deployed; results bucket `{ctx.deployment.results_bucket}`")
    st.table([{"agent": n, "models": ", ".join(ctx.cfg.agents[n].models), "default": e.default_model} for n, e in ctx.deployment.agents.items()])
st.markdown("Use the pages on the left: **Playground** to chat, **Benchmarks** to compare models, **Costs** to see spend.")
