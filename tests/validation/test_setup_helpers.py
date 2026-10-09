from decimal import Decimal

import pytest

from template_core.config import load_config
from template_core.pricing import load_pricing
from validation.cli import select_gates
from validation.spend import g7_spec, scale_prices, validation_spend_estimate


def test_select_gates():
    assert select_gates("all", None)[0] == "g0" and select_gates("all", None)[-1] == "g10"
    assert select_gates("all", "g9")[-1] == "g9"


@pytest.mark.skip(reason="g11 registered in Task 18")
def test_select_single_gate_g11():
    assert select_gates("g11", None) == ["g11"]


def test_g7_spec_uses_tools_agent_first_three_models_two_repeats():
    cfg = load_config()
    spec = g7_spec(cfg)
    assert spec.agent == "tools-agent" and spec.models == cfg.agents["tools-agent"].models[:3] and spec.repeats == 2
    assert len(spec.suite.cases) == 3


def test_spend_estimate_is_under_budget_and_scaling_doubles():
    cfg, prices = load_config(), load_pricing()
    assert Decimal(0) < validation_spend_estimate(cfg, prices) < Decimal(5)
    doubled = scale_prices(prices, 2)
    assert doubled.models["haiku-4-5"].input == prices.models["haiku-4-5"].input * 2
