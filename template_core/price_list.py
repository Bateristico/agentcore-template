"""AWS Price List lookups used by scripts/refresh_pricing.py (spec §8 item 5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from template_core.pricing import PriceTable

UNIT_FACTORS = {"1M tokens": Decimal(1), "1K tokens": Decimal(1000)}  # multiply to get USD per 1M tokens


class PriceListError(RuntimeError):
    pass


def fetch_usage_type_price(client, service_code: str, region: str, usage_type: str, model: str | None = None) -> Decimal | None:
    resp = client.get_products(
        ServiceCode=service_code,
        Filters=[
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            {"Type": "TERM_MATCH", "Field": "usagetype", "Value": usage_type},
        ],
        MaxResults=100,
    )
    for item in resp.get("PriceList", []):
        product = json.loads(item)
        if model and product.get("product", {}).get("attributes", {}).get("model") != model:
            continue
        for term in product.get("terms", {}).get("OnDemand", {}).values():
            for dim in term.get("priceDimensions", {}).values():
                unit = dim.get("unit")
                if unit not in UNIT_FACTORS:
                    raise PriceListError(f"unknown unit '{unit}' for {usage_type}")
                return (Decimal(dim["pricePerUnit"]["USD"]) * UNIT_FACTORS[unit]).normalize()
    return None


@dataclass(frozen=True)
class PriceDiff:
    alias: str
    component: str
    current: Decimal
    fetched: Decimal | None

    @property
    def status(self) -> str:
        if self.fetched is None:
            return "not-found"
        return "same" if self.fetched == self.current else "changed"


def compare_prices(table: PriceTable, client, region: str) -> list[PriceDiff]:
    diffs: list[PriceDiff] = []
    for alias, price in table.models.items():
        if price.source is None:
            continue
        for component, usage_type in price.source.usage_types.items():
            fetched = fetch_usage_type_price(client, price.source.service_code, region, usage_type, price.source.model)
            diffs.append(PriceDiff(alias, component, getattr(price, component), fetched))
    return diffs


def apply_prices(table: PriceTable, diffs: list[PriceDiff], today: date) -> PriceTable:
    models = dict(table.models)
    for d in diffs:
        if d.status == "changed":
            models[d.alias] = models[d.alias].model_copy(update={d.component: d.fetched})
    return table.model_copy(update={"models": models, "as_of": today})
