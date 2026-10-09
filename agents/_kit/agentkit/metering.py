"""Token usage extraction and CloudWatch EMF emission (spec §3). Never computes dollars."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from typing import Any

NAMESPACE = "AgentTemplate"
METRIC_UNITS = {
    "InputTokens": "Count",
    "OutputTokens": "Count",
    "CacheReadTokens": "Count",
    "CacheWriteTokens": "Count",
    "LatencyMs": "Milliseconds",
    "Cycles": "Count",
    "ToolCalls": "Count",
    "Errors": "Count",
}
_COUNTERS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "latency_ms", "cycles", "tool_calls")


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    cycles: int = 0
    tool_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Usage:
        return cls(model=str(data["model"]), **{k: int(data.get(k) or 0) for k in _COUNTERS})

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.model, **{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self) if f.name != "model"})


def usage_from_metrics(model_alias: str, metrics: Any, latency_ms: int) -> Usage:
    """Build Usage from a Strands EventLoopMetrics (or None when the call failed before any model response)."""
    usage = getattr(metrics, "accumulated_usage", None) or {}
    tool_metrics = getattr(metrics, "tool_metrics", None) or {}
    return Usage(
        model=model_alias,
        input_tokens=int(usage.get("inputTokens", 0) or 0),
        output_tokens=int(usage.get("outputTokens", 0) or 0),
        cache_read_tokens=int(usage.get("cacheReadInputTokens", 0) or 0),
        cache_write_tokens=int(usage.get("cacheWriteInputTokens", 0) or 0),
        latency_ms=int(latency_ms),
        cycles=int(getattr(metrics, "cycle_count", 0) or 0),
        tool_calls=sum(int(getattr(t, "call_count", 0) or 0) for t in tool_metrics.values()),
    )


def emf_record(
    agent: str,
    usage: Usage,
    *,
    errors: int,
    run_id: str | None = None,
    case_id: str | None = None,
    session_id: str | None = None,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "_aws": {
            "Timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [["Agent", "Model"]],
                    "Metrics": [{"Name": name, "Unit": unit} for name, unit in METRIC_UNITS.items()],
                }
            ],
        },
        "Agent": agent,
        "Model": usage.model,
        "InputTokens": usage.input_tokens,
        "OutputTokens": usage.output_tokens,
        "CacheReadTokens": usage.cache_read_tokens,
        "CacheWriteTokens": usage.cache_write_tokens,
        "LatencyMs": usage.latency_ms,
        "Cycles": usage.cycles,
        "ToolCalls": usage.tool_calls,
        "Errors": errors,
        "run_id": run_id,
        "case_id": case_id,
        "session_id": session_id,
    }


def _stdout(line: str) -> None:
    print(line, flush=True)


def emit(record: dict[str, Any], write: Callable[[str], Any] | None = None) -> None:
    """Write one EMF line; AgentCore ships container stdout to CloudWatch Logs, which extracts the metrics."""
    (write or _stdout)(json.dumps(record, separators=(",", ":")))
