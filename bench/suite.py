"""Test suite files: bench/suites/<agent>/<suite>.yaml (spec §5)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from template_core.config import REPO_ROOT, ConfigError

SUITES_ROOT = REPO_ROOT / "bench" / "suites"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    prompt: str = Field(min_length=1)
    expected: str | None = None
    rubric: str | None = None


class Estimate(BaseModel):
    """Typical tokens per agent call, used for the pre-run cost estimate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=2000, ge=0)
    output_tokens: int = Field(default=500, ge=0)


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite: str
    estimate: Estimate = Estimate()
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Suite:
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                raise ValueError(f"duplicate case id '{case.id}'")
            seen.add(case.id)
        return self


def suite_path(agent: str, name: str, root: Path = SUITES_ROOT) -> Path:
    return Path(root) / agent / f"{name}.yaml"


def list_suites(agent: str, root: Path = SUITES_ROOT) -> list[str]:
    folder = Path(root) / agent
    return sorted(p.stem for p in folder.glob("*.yaml")) if folder.is_dir() else []


def load_suite(agent: str, name: str, root: Path = SUITES_ROOT) -> Suite:
    path = suite_path(agent, name, root)
    if not path.exists():
        raise ConfigError(f"suite not found: {path} (available: {', '.join(list_suites(agent, root)) or 'none'})")
    try:
        return Suite.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"invalid suite {path}: {exc}") from exc
