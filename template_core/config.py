"""Deployment configuration: loads and validates config/deploy.yaml (spec §4).

The TypeScript twin lives in infra/lib/config.ts; both validate config/fixtures/*.yaml.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "deploy.yaml"

PREFIX_RE = r"^[a-z][a-z0-9]{1,11}$"
REGION_RE = r"^[a-z]{2}(-[a-z]+)+-\d$"
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
ALIAS_RE = re.compile(r"^[a-z0-9]+([.-][a-z0-9]+)*$")
RUNTIME_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
ALIAS_MAX = 24
PROFILE_NAME_MAX = 64
GEO_PREFIXES = frozenset({"eu", "us", "apac", "global", "jp", "au", "ca", "us-gov"})


class ConfigError(ValueError):
    """deploy.yaml is missing or invalid; the message says what to fix."""


def _account_to_str(value: object) -> object:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


class ModelCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(min_length=3)
    billing_tag: bool = True

    @property
    def foundation_model_id(self) -> str:
        head, _, rest = self.profile.partition(".")
        return rest if head in GEO_PREFIXES and rest else self.profile

    @property
    def family(self) -> str:
        return self.foundation_model_id.split(".", 1)[0]


class AgentCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(min_length=1)
    default: str


class DeployConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(pattern=PREFIX_RE)
    region: str = Field(pattern=REGION_RE)
    account: Annotated[str | None, BeforeValidator(_account_to_str)] = Field(default=None, pattern=r"^\d{12}$")
    removal: Literal["retain", "destroy"] = "retain"
    models: dict[str, ModelCfg] = Field(min_length=1)
    judge: str
    agents: dict[str, AgentCfg] = Field(min_length=1)

    @model_validator(mode="after")
    def _cross_checks(self) -> DeployConfig:
        problems: list[str] = []
        for alias in self.models:
            if not ALIAS_RE.match(alias) or len(alias) > ALIAS_MAX:
                problems.append(f"model alias '{alias}' must match {ALIAS_RE.pattern} and be at most {ALIAS_MAX} chars")
        if self.judge not in self.models:
            problems.append(f"judge '{self.judge}' is not a defined model alias")
        for name, agent in self.agents.items():
            if not NAME_RE.match(name):
                problems.append(f"agent name '{name}' must match {NAME_RE.pattern}")
                continue
            if not RUNTIME_NAME_RE.match(self.runtime_name(name)):
                problems.append(
                    f"runtime name '{self.runtime_name(name)}' for agent '{name}' is longer than 48 chars or invalid"
                )
            for alias in agent.models:
                if alias not in self.models:
                    problems.append(f"agent '{name}' uses unknown model alias '{alias}'")
                elif len(self.profile_name(name, alias)) > PROFILE_NAME_MAX:
                    problems.append(f"profile name '{self.profile_name(name, alias)}' exceeds {PROFILE_NAME_MAX} chars")
            if agent.default not in agent.models:
                problems.append(f"agent '{name}' default '{agent.default}' is not in its models list")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def runtime_name(self, agent: str) -> str:
        return f"{self.prefix}_{agent.replace('-', '_')}"

    def agent_stack_name(self, agent: str) -> str:
        return f"{self.prefix}-agent-{agent}"

    def profile_name(self, agent: str, alias: str) -> str:
        return f"{self.prefix}-{agent}-{alias}"

    @property
    def platform_stack_name(self) -> str:
        return f"{self.prefix}-platform"

    @property
    def judge_profile_name(self) -> str:
        return f"{self.prefix}-judge"

    @property
    def dashboard_name(self) -> str:
        return f"{self.prefix}-agent-costs"


def parse_config(text: str, **overrides: object) -> DeployConfig:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"deploy.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("deploy.yaml must be a mapping")
    data.update({k: v for k, v in overrides.items() if v is not None})
    try:
        return DeployConfig.model_validate(data)
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "config"
            parts.append(f"{loc}: {err['msg']}")
        raise ConfigError("invalid deploy.yaml: " + "; ".join(parts)) from exc


def load_config(path: Path | str = DEFAULT_CONFIG, **overrides: object) -> DeployConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    return parse_config(p.read_text(encoding="utf-8"), **overrides)
