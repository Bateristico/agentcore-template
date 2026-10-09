import queue
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
import streamlit.components.v1 as components
from botocore.exceptions import BotoCoreError, ClientError

from bench.orchestrate import execute_run
from bench.report import build_report, summary_table
from bench.runner import RunSpec, estimate_cost, new_run_id
from bench.suite import list_suites, load_suite
from template_core.pricing import PriceTable, format_usd
from ui.context import get_context

HISTORY_ERRORS = (ClientError, BotoCoreError, FileNotFoundError, KeyError, ValueError)

st.set_page_config(page_title="Benchmarks", layout="wide")
ctx = get_context()
st.title("Benchmarks")
tab_run, tab_history = st.tabs(["Run", "History"])

with tab_run:
    if ctx.client is None:
        st.error(f"Agents are not deployed: {ctx.deployment_error}")
    else:
        agent = st.selectbox("Agent", list(ctx.cfg.agents), key="bm_agent")
        suites = list_suites(agent)
        if not suites:
            st.info(f"No suites yet: add bench/suites/{agent}/<name>.yaml")
        else:
            suite = load_suite(agent, st.selectbox("Suite", suites, key="bm_suite"))
            allowed = ctx.cfg.agents[agent].models
            models = st.multiselect("Models", allowed, default=allowed, key=f"bm_models_{agent}")
            c1, c2, c3 = st.columns(3)
            repeats = int(c1.number_input("Repeats", 1, 10, 2))
            concurrency = int(c2.number_input("Concurrency", 1, 16, 4))
            use_judge = c3.checkbox("Score with the judge", value=True)
            draft = RunSpec("draft", agent, suite, models, repeats, concurrency)
            estimate = estimate_cost(draft, ctx.prices, ctx.cfg.judge if use_judge else None) if models else None
            st.metric("Estimated cost", format_usd(estimate) if estimate is not None else "-")
            st.caption(f"{len(draft.tasks())} agent calls")
            accepted = st.checkbox(f"I accept the estimated cost of {format_usd(estimate) if estimate is not None else '-'}")
            if st.button("Run benchmark", disabled=not (models and accepted), type="primary"):
                spec = RunSpec(new_run_id(agent), agent, suite, models, repeats, concurrency)
                total, finished, done = len(spec.tasks()), 0, queue.Queue()
                bar = st.progress(0.0, text=f"0/{total} calls")
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        execute_run, spec, cfg=ctx.cfg, prices=ctx.prices, deployment=ctx.deployment, client=ctx.client,
                        judge=ctx.judge if use_judge else None, store=ctx.store, estimate=estimate,
                        cloudwatch=ctx.cloudwatch, on_row=done.put,
                    )
                    while not future.done() or not done.empty():
                        try:
                            done.get(timeout=0.5)
                            finished += 1
                            bar.progress(finished / total, text=f"{finished}/{total} calls")
                        except queue.Empty:
                            pass
                    try:
                        outcome = future.result()
                    except Exception as exc:
                        outcome = None
                        st.error(f"Benchmark failed: {type(exc).__name__}: {exc}")
                if outcome is not None:
                    st.success(f"Run {outcome.meta['run_id']} saved")
                    for line in outcome.locations:
                        if line.startswith("WARNING"):
                            st.warning(line)
                    st.dataframe(summary_table(outcome.summaries), use_container_width=True)
                    st.download_button("Download report.html", outcome.html, file_name=f"{outcome.meta['run_id']}.html", mime="text/html")

with tab_history:
    try:
        runs = ctx.store.list_runs()
    except HISTORY_ERRORS as exc:
        runs = []
        st.error(f"Could not list runs: {exc}")
    if not runs:
        st.info("No benchmark runs yet.")
    else:
        st.dataframe(
            [{"run": m["run_id"], "agent": m.get("agent"), "suite": m.get("suite"), "models": ", ".join(m.get("models", [])),
              "created": m.get("created_at"), "estimate": m.get("estimate_usd")} for m in runs],
            use_container_width=True,
        )
        run_id = st.selectbox("Open run", [m["run_id"] for m in runs], key="bm_history_run")
        reprice = st.toggle("Re-price with current pricing.yaml", value=False)
        try:
            meta, rows = ctx.store.load(run_id)
            prices = ctx.prices if reprice else PriceTable.model_validate(meta["pricing"])
            html, summaries = build_report(meta, rows, prices, ctx.cfg, None, repriced=reprice)
        except HISTORY_ERRORS as exc:
            st.error(f"Could not open run {run_id}: {type(exc).__name__}: {exc}")
        else:
            st.dataframe(summary_table(summaries), use_container_width=True)
            st.download_button("Download report.html", html, file_name=f"{run_id}.html", mime="text/html")
            components.html(html, height=900, scrolling=True)
