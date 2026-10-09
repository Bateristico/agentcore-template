from datetime import date
from decimal import Decimal

import pytest

from agentkit.metering import Usage
from template_core.config import ConfigError, load_config
from template_core.pricing import (
    PriceTable,
    UnpricedModel,
    dump_pricing,
    format_usd,
    load_pricing,
)


def table() -> PriceTable:
    return PriceTable.model_validate(
        {
            "as_of": date(2026, 10, 9),
            "agentcore": {"vcpu_hour": 0.0895, "gb_hour": 0.00945},
            "models": {"haiku-4-5": {"input": 1.1, "output": 5.5, "cache_read": 0.11, "cache_write": 1.375}},
        }
    )


def test_repo_pricing_covers_repo_config():
    prices = load_pricing()
    assert prices.missing(load_config().models) == []


def test_golden_cost():
    usage = Usage("haiku-4-5", input_tokens=1_000_000, output_tokens=200_000, cache_read_tokens=500_000)
    cost = table().model_cost("haiku-4-5", usage)
    assert cost.input == Decimal("1.1")
    assert cost.output == Decimal("1.1")
    assert cost.cache_read == Decimal("0.055")
    assert cost.cache_write == Decimal("0")
    assert cost.total == Decimal("2.255")


def test_floats_become_exact_decimals():
    assert table().models["haiku-4-5"].input == Decimal("1.1")


def test_unpriced_model():
    with pytest.raises(UnpricedModel):
        table().model_cost("nope", Usage("nope", 1))
    assert table().try_model_cost("nope", Usage("nope", 1)) is None
    assert table().missing(["haiku-4-5", "nope"]) == ["nope"]


def test_agentcore_cost():
    assert table().agentcore_cost(2, 10) == Decimal("0.0895") * 2 + Decimal("0.00945") * 10


def test_negative_price_rejected(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("as_of: 2026-10-09\nagentcore: {vcpu_hour: 1, gb_hour: 1}\nmodels: {m: {input: -1, output: 1}}\n")
    with pytest.raises(ConfigError, match="pricing"):
        load_pricing(p)


def test_dump_round_trips(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(dump_pricing(load_pricing()), encoding="utf-8")
    assert load_pricing(p) == load_pricing()


@pytest.mark.parametrize(
    ("value", "text"),
    [(Decimal("0.000123"), "$0.000123"), (Decimal("0.5"), "$0.5000"), (Decimal("12.345"), "$12.35"), (None, "unpriced")],
)
def test_format_usd(value, text):
    assert format_usd(value) == text
