from datetime import date
from decimal import Decimal

from agentkit.metering import Usage
from bench.orchestrate import execute_run, rerender
from bench.records import Verdict
from bench.runner import RunSpec
from bench.store import RunStore
from bench.suite import Case, Suite
from template_core.agentcore import InvokeResult
from template_core.config import parse_config
from template_core.deployment import AgentEndpoint, Deployment
from template_core.pricing import PriceTable

CFG = parse_config(
    "prefix: t1\nregion: eu-central-1\nmodels:\n  m1: { profile: eu.amazon.nova-pro-v1:0 }\n"
    "judge: m1\nagents:\n  a: { models: [m1], default: m1 }\n"
)
PRICES = PriceTable.model_validate(
    {"as_of": date(2026, 10, 9), "agentcore": {"vcpu_hour": 1, "gb_hour": 1}, "models": {"m1": {"input": 1.0, "output": 5.0}}}
)
DEP = Deployment("t1", "b", "m1", "arn:j", {"a": AgentEndpoint("a", "arn:rt", "rt", {"m1": "arn:p"}, "m1")})


class FakeClient:
    def invoke(self, agent, prompt, *, model=None, run_id=None, case_id=None, session_id=None):
        return InvokeResult(agent, model, "s" * 40, f"answer to {prompt}", Usage(model, 1000, 100, latency_ms=10), None, 20)


class FakeJudge:
    alias = "m1"

    def grade(self, case, response):
        return Verdict(4, "fine", Usage("m1", 100, 10), None)


def test_execute_run_saves_and_rerender_reprices(tmp_path):
    spec = RunSpec("r1", "a", Suite(suite="s", cases=[Case(id="c1", prompt="p")]), ["m1"], 2)
    store = RunStore(local_root=tmp_path)
    outcome = execute_run(spec, cfg=CFG, prices=PRICES, deployment=DEP, client=FakeClient(), judge=FakeJudge(),
                          store=store, estimate=Decimal("0.01"))
    assert len(outcome.rows) == 2 and all(r.score == 4 for r in outcome.rows)
    assert outcome.meta["runtime_arn"] == "arn:rt" and outcome.meta["pricing"]["models"]["m1"]["input"] == "1.0"
    assert outcome.summaries[0].total_cost == Decimal("0.003")
    doubled = PRICES.model_copy(update={"models": {"m1": PRICES.models["m1"].model_copy(update={"input": Decimal(2), "output": Decimal(10)})}})
    _, summaries = rerender("r1", store=store, cfg=CFG, prices=doubled)
    assert summaries[0].total_cost == Decimal("0.006")
    _, snapshot = rerender("r1", store=store, cfg=CFG)
    assert snapshot[0].total_cost == Decimal("0.003")
