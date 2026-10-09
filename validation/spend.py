"""What a validation run costs, and the G7 benchmark it runs."""

from __future__ import annotations

from decimal import Decimal

from agentkit.metering import Usage
from bench.calibration import load_calibration
from bench.runner import JUDGE_ESTIMATE, RunSpec, estimate_cost, new_run_id
from bench.suite import load_suite
from template_core.config import DeployConfig
from template_core.pricing import COMPONENTS, PriceTable

SMOKE_USAGE = Usage("", input_tokens=1000, output_tokens=50)  # one "pong" call incl. system prompt and tool specs


def g7_spec(cfg: DeployConfig) -> RunSpec:
    agent = "tools-agent" if "tools-agent" in cfg.agents else next(iter(cfg.agents))
    return RunSpec(new_run_id(agent), agent, load_suite(agent, "basics"), cfg.agents[agent].models[:3], repeats=2)


def scale_prices(prices: PriceTable, factor: int | Decimal) -> PriceTable:
    models = {
        alias: price.model_copy(update={c: getattr(price, c) * factor for c in COMPONENTS})
        for alias, price in prices.models.items()
    }
    return prices.model_copy(update={"models": models})


def validation_spend_estimate(cfg: DeployConfig, prices: PriceTable) -> Decimal:
    pairs = [model for a in cfg.agents.values() for model in a.models]
    total = sum((prices.model_cost(m, SMOKE_USAGE).total for m in pairs), Decimal(0)) * 2  # G4 and G9
    total += sum((prices.model_cost(alias, SMOKE_USAGE).total for alias in cfg.models), Decimal(0))  # G0 probes
    total += prices.model_cost(cfg.judge, JUDGE_ESTIMATE).total * 2 * len(load_calibration())  # G6
    total += estimate_cost(g7_spec(cfg), prices, cfg.judge)  # G7
    return total
