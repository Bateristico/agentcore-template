import pytest
from botocore.exceptions import ClientError
from streamlit.testing.v1 import AppTest

from agentkit.metering import Usage
from bench.records import Row
from bench.store import RunStore
from template_core.agentcore import InvokeResult
from template_core.config import REPO_ROOT, load_config
from template_core.deployment import AgentEndpoint, Deployment
from template_core.pricing import format_usd, load_pricing
from ui.context import OVERRIDE_KEY, Context, result_metrics

PAGES = REPO_ROOT / "ui"


class FakeClient:
    def invoke(self, agent, prompt, *, model=None, run_id=None, case_id=None, session_id=None):
        return InvokeResult(agent, model, "s" * 40, "pong", Usage(model, 1000, 100, latency_ms=500), None, 600)


class FakeCW:
    def get_metric_data(self, MetricDataQueries, StartTime, EndTime, NextToken=None):
        return {"MetricDataResults": [{"Id": q["Id"], "Values": []} for q in MetricDataQueries]}


class DeniedCE:
    def list_cost_allocation_tags(self, TagKeys):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "linked"}}, "ListCostAllocationTags")

    def get_cost_and_usage(self, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetCostAndUsage")


@pytest.fixture
def ctx(tmp_path):
    cfg, prices = load_config(), load_pricing()
    deployment = Deployment(cfg.prefix, "bucket", cfg.judge, "arn:judge", {
        name: AgentEndpoint(name, f"arn:rt/{name}", name, {m: f"arn:p/{m}" for m in a.models}, a.default)
        for name, a in cfg.agents.items()
    })
    store = RunStore(local_root=tmp_path)
    meta = {"run_id": "tools-agent-20261009T120000Z-ab12", "agent": "tools-agent", "suite": "basics", "models": ["haiku-4-5"],
            "repeats": 1, "judge": cfg.judge, "created_at": "2026-10-09T12:00:00Z", "estimate_usd": "0.01",
            "pricing": prices.model_dump(mode="json"), "config": cfg.model_dump(mode="json")}
    usage = {"model": "haiku-4-5", "input_tokens": 1000, "output_tokens": 100, "latency_ms": 500}
    store.save(meta, [Row(meta["run_id"], "tools-agent", "haiku-4-5", "c1", 1, "s", "ok", usage, None, 600, 1, 5, "fine")], "<html></html>")
    return Context(cfg, prices, None, deployment, None, FakeClient(), None, store, FakeCW(), DeniedCE())


def run_page(path, context):
    at = AppTest.from_file(str(path), default_timeout=30)
    at.session_state[OVERRIDE_KEY] = context
    at.run()
    assert not at.exception, at.exception
    return at


def test_result_metrics_cost_matches_cli_format(ctx):
    r = FakeClient().invoke("tools-agent", "x", model="haiku-4-5")
    expected = format_usd(ctx.prices.model_cost("haiku-4-5", r.usage).total)
    assert result_metrics(r, ctx.prices)["cost"] == expected == "$0.001650"


def test_home_reports_missing_deployment(ctx):
    ctx.deployment, ctx.client, ctx.deployment_error = None, None, "stack tmpl-platform is not deployed"
    at = run_page(PAGES / "app.py", ctx)
    assert any("not deployed" in e.value for e in at.error)


def test_playground_shows_answer_and_cost(ctx):
    at = run_page(PAGES / "pages" / "1_Playground.py", ctx)
    at.chat_input[0].set_value("ping").run()
    assert not at.exception
    assert any("pong" in m.value for m in at.markdown)
    assert any(m.value == "$0.001650" for m in at.metric)


def test_benchmarks_history_lists_saved_run(ctx):
    at = run_page(PAGES / "pages" / "2_Benchmarks.py", ctx)
    options = [o for sb in at.selectbox for o in sb.options]
    assert "tools-agent-20261009T120000Z-ab12" in options


def test_costs_page_explains_missing_access(ctx):
    at = run_page(PAGES / "pages" / "3_Costs.py", ctx)
    assert any("not readable" in w.value for w in at.warning)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _accept_box(at):
    return next(c for c in at.checkbox if c.label.startswith("I accept"))


def test_benchmarks_run_requires_acceptance_then_saves_run(ctx):
    at = run_page(PAGES / "pages" / "2_Benchmarks.py", ctx)
    assert _button(at, "Run benchmark").disabled
    _accept_box(at).check().run()
    assert not at.exception
    assert not _button(at, "Run benchmark").disabled
    _button(at, "Run benchmark").click().run()
    assert not at.exception, at.exception
    assert any(s.value.startswith("Run hello-agent-") and s.value.endswith("saved") for s in at.success)
    saved = [m["run_id"] for m in ctx.store.list_runs()]
    assert any(r.startswith("hello-agent-") for r in saved)


def test_benchmarks_run_failure_is_shown_without_traceback(ctx):
    class BoomStore:
        def save(self, *a, **k):
            raise RuntimeError("kaput")

        def list_runs(self, limit=100):
            return []

    ctx.store = BoomStore()
    at = run_page(PAGES / "pages" / "2_Benchmarks.py", ctx)
    _accept_box(at).check().run()
    _button(at, "Run benchmark").click().run()
    assert not at.exception
    assert any(e.value == "Benchmark failed: RuntimeError: kaput" for e in at.error)
    assert not at.success


def test_benchmarks_history_error_does_not_break_run_tab(ctx):
    class BadStore:
        def list_runs(self, limit=100):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "ListObjects")

    ctx.store = BadStore()
    at = run_page(PAGES / "pages" / "2_Benchmarks.py", ctx)
    assert any("Could not list runs" in e.value for e in at.error)
    assert any(b.label == "Run benchmark" for b in at.button)


def test_costs_page_survives_cloudwatch_failure(ctx):
    class BadCW:
        def get_metric_data(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetMetricData")

    ctx.cloudwatch = BadCW()
    at = run_page(PAGES / "pages" / "3_Costs.py", ctx)
    assert any("not readable" in e.value for e in at.error)
    assert any("Billed" in s.value for s in at.subheader)
