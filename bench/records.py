"""Plain records shared by the runner, judge, report and store."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from agentkit.metering import Usage
from bench.suite import Case


@dataclass(frozen=True)
class Task:
    model: str
    case: Case
    repeat: int


@dataclass(frozen=True)
class Verdict:
    score: int | None
    reason: str | None
    usage: Usage | None
    error: str | None


@dataclass
class Row:
    run_id: str
    agent: str
    model: str
    case_id: str
    repeat: int
    session_id: str | None
    output: str | None
    usage: dict[str, Any] | None
    error: dict[str, Any] | None
    client_latency_ms: int
    attempts: int
    score: int | None = None
    reason: str | None = None
    judge_usage: dict[str, Any] | None = None
    judge_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Row:
        return cls(**{f.name: data.get(f.name) for f in fields(cls)})
