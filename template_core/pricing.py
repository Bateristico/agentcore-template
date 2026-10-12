"""Price table (config/pricing.yaml) and cost math. Dollars are computed at read time (spec §3)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from agentkit.metering import Usage
from template_core.config import REPO_ROOT, ConfigError

DEFAULT_PRICING = REPO_ROOT / "config" / "pricing.yaml"
PER_MILLION = Decimal(1_000_000)
Component = Literal["input", "output", "cache_read", "cache_write"]
COMPONENTS: tuple[Component, ...] = ("input", "output", "cache_read", "cache_write")
HEADER = (
    "# USD per 1M tokens (AgentCore: per vCPU-hour / GB-hour). Regenerate with:\n"
    "#   uv run scripts/refresh_pricing.py --profile <profile> --write\n"
)


def _to_decimal(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("price must be a number")
    if isinstance(value, int | float):
        return Decimal(str(value))
    return value


Money = Annotated[Decimal, BeforeValidator(_to_decimal), Field(ge=0)]


class PriceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_code: str
    model: str | None = None  # Price List "model" attribute, needed when usage types are shared across models
    usage_types: dict[Component, str]


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: Money
    output: Money
    cache_read: Money = Decimal(0)
    cache_write: Money = Decimal(0)
    source: PriceSource | None = None


class AgentCorePrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vcpu_hour: Money
    gb_hour: Money


class CostBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal

    @property
    def total(self) -> Decimal:
        return self.input + self.output + self.cache_read + self.cache_write


class UnpricedModel(KeyError):
    pass


class PriceTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: Literal["USD"] = "USD"
    as_of: date
    agentcore: AgentCorePrice
    models: dict[str, ModelPrice]

    def missing(self, aliases: Iterable[str]) -> list[str]:
        return [a for a in aliases if a not in self.models]

    def model_cost(self, alias: str, usage: Usage) -> CostBreakdown:
        price = self.models.get(alias)
        if price is None:
            raise UnpricedModel(alias)
        return CostBreakdown(
            input=price.input * usage.input_tokens / PER_MILLION,
            output=price.output * usage.output_tokens / PER_MILLION,
            cache_read=price.cache_read * usage.cache_read_tokens / PER_MILLION,
            cache_write=price.cache_write * usage.cache_write_tokens / PER_MILLION,
        )

    def try_model_cost(self, alias: str, usage: Usage) -> CostBreakdown | None:
        try:
            return self.model_cost(alias, usage)
        except UnpricedModel:
            return None

    def agentcore_cost(self, vcpu_hours: float | Decimal, gb_hours: float | Decimal) -> Decimal:
        return self.agentcore.vcpu_hour * Decimal(str(vcpu_hours)) + self.agentcore.gb_hour * Decimal(str(gb_hours))


def load_pricing(path: Path | str = DEFAULT_PRICING) -> PriceTable:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"pricing file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return PriceTable.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"invalid pricing file {p}: {exc}") from exc


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def dump_pricing(table: PriceTable) -> str:
    return HEADER + yaml.safe_dump(_plain(table.model_dump()), sort_keys=False, allow_unicode=True)


def format_usd(value: Decimal | None) -> str:
    if value is None:
        return "unpriced"
    if value < Decimal("0.01"):
        places = 6
    elif value < 1:
        places = 4
    else:
        places = 2
    return "$" + str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
