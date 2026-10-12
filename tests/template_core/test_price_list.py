import json
from datetime import date
from decimal import Decimal

import pytest

from template_core.price_list import PriceDiff, PriceListError, apply_prices, compare_prices, fetch_usage_type_price
from template_core.pricing import PriceTable


def product(usd: str, unit: str, model: str = "Claude Haiku 4.5") -> str:
    return json.dumps(
        {
            "product": {"attributes": {"model": model}},
            "terms": {"OnDemand": {"t": {"priceDimensions": {"d": {"unit": unit, "pricePerUnit": {"USD": usd}}}}}},
        }
    )


class FakePricing:
    def __init__(self, items_by_usage_type):
        self.items = items_by_usage_type
        self.calls = []

    def get_products(self, **kwargs):
        self.calls.append(kwargs)
        ut = next(f["Value"] for f in kwargs["Filters"] if f["Field"] == "usagetype")
        return {"PriceList": self.items.get(ut, [])}


def test_per_1k_unit_is_scaled_to_per_1m():
    client = FakePricing({"ut": [product("0.0004290000", "1K tokens", model="Nova 2.0 Lite")]})
    assert fetch_usage_type_price(client, "AmazonBedrock", "eu-central-1", "ut") == Decimal("0.429")


def test_model_filter_picks_matching_product():
    client = FakePricing({"ut": [product("3.3", "1M tokens", "Claude Sonnet 4.6"), product("1.1", "1M tokens")]})
    assert fetch_usage_type_price(client, "S", "eu-central-1", "ut", model="Claude Haiku 4.5") == Decimal("1.1")


def test_not_found_returns_none():
    assert fetch_usage_type_price(FakePricing({}), "S", "eu-central-1", "ut") is None


def test_unknown_unit_raises():
    with pytest.raises(PriceListError, match="unit"):
        fetch_usage_type_price(FakePricing({"ut": [product("1", "Images")]}), "S", "eu-central-1", "ut")


def test_compare_and_apply():
    t = PriceTable.model_validate(
        {
            "as_of": date(2026, 1, 1),
            "agentcore": {"vcpu_hour": 1, "gb_hour": 1},
            "models": {
                "m": {
                    "input": 1.1,
                    "output": 5.0,
                    "source": {"service_code": "S", "model": "Claude Haiku 4.5", "usage_types": {"input": "in", "output": "out"}},
                }
            },
        }
    )
    client = FakePricing({"in": [product("1.1", "1M tokens")], "out": [product("5.5", "1M tokens")]})
    diffs = compare_prices(t, client, "eu-central-1")
    assert [(d.component, d.status) for d in diffs] == [("input", "same"), ("output", "changed")]
    updated = apply_prices(t, diffs, date(2026, 10, 9))
    assert updated.models["m"].output == Decimal("5.5") and updated.as_of == date(2026, 10, 9)
    assert PriceDiff("m", "input", Decimal(1), None).status == "not-found"
