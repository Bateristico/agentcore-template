"""Model alias → model id resolution (spec §3). MODEL_PROFILES and DEFAULT_MODEL are set by CDK."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass


class KitConfigError(RuntimeError):
    """The runtime environment is misconfigured (a deployment bug, not a caller error)."""


class ModelNotAllowed(Exception):
    def __init__(self, requested: str, allowed: Mapping[str, str] | list[str]):
        self.requested = requested
        self.allowed = sorted(allowed)
        super().__init__(f"model '{requested}' is not allowed for this agent; allowed: {', '.join(self.allowed)}")


@dataclass(frozen=True)
class ModelChoice:
    alias: str
    model_id: str  # application inference profile ARN, or a system profile id when billing_tag is false


def load_model_profiles(env: Mapping[str, str]) -> dict[str, str]:
    raw = env.get("MODEL_PROFILES")
    if not raw:
        raise KitConfigError("MODEL_PROFILES env var is not set")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KitConfigError(f"MODEL_PROFILES is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not data or not all(
        isinstance(k, str) and isinstance(v, str) and v for k, v in data.items()
    ):
        raise KitConfigError("MODEL_PROFILES must be a non-empty JSON object of alias -> model id")
    return data


def resolve_model(requested: str | None, env: Mapping[str, str] = os.environ) -> ModelChoice:
    profiles = load_model_profiles(env)
    alias = requested or env.get("DEFAULT_MODEL")
    if not alias:
        raise KitConfigError("no model requested and DEFAULT_MODEL is not set")
    if alias not in profiles:
        raise ModelNotAllowed(alias, profiles)
    return ModelChoice(alias, profiles[alias])


def make_bedrock_model(choice: ModelChoice, region: str | None = None):
    from strands.models import BedrockModel  # imported lazily so tests and tooling don't need strands loaded

    return BedrockModel(model_id=choice.model_id, region_name=region or os.environ.get("AWS_REGION"))
