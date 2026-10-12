import streamlit as st

from ui.context import get_context, result_metrics

st.set_page_config(page_title="Playground", layout="wide")
ctx = get_context()
st.title("Playground")
if ctx.client is None:
    st.error(f"Agents are not deployed: {ctx.deployment_error}")
    st.stop()

agent = st.selectbox("Agent", list(ctx.cfg.agents), key="pg_agent")
allowed = ctx.cfg.agents[agent].models
model = st.selectbox("Model", allowed, index=allowed.index(ctx.cfg.agents[agent].default), key=f"pg_model_{agent}")
st.caption("Each message is an independent call: agents keep no memory between calls.")
history = st.session_state.setdefault("pg_history", [])
if st.button("Clear conversation"):
    history.clear()


def render(item: dict) -> None:
    with st.chat_message("user"):
        st.markdown(f"**{item['agent']} · {item['model']}** — {item['prompt']}")
    with st.chat_message("assistant"):
        r = item["result"]
        if r.error:
            st.error(f"{r.error.get('type')}: {r.error.get('message')}")
        if r.output is not None:
            st.markdown(r.output)
        for col, (label, value) in zip(st.columns(6), result_metrics(r, ctx.prices).items(), strict=True):
            col.metric(label, value)


for item in history:
    render(item)

prompt = st.chat_input("Ask the agent…")
if prompt:
    with st.spinner(f"{agent} on {model}…"):
        result = ctx.client.invoke(agent, prompt, model=model)
    history.append({"agent": agent, "model": model, "prompt": prompt, "result": result})
    render(history[-1])
