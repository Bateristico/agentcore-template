"""AgentCore entrypoint shared by every agent (spec §3).

Loads agents/<name>/agent.py, and wraps each request with model resolution, a fresh Strands Agent,
usage metering (EMF) and error handling. Run locally: `uv run python -m agentkit.server`.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

from agentkit.metering import DEFAULT_PROJECT, emf_record, emit, usage_from_metrics
from agentkit.models import KitConfigError, ModelNotAllowed, make_bedrock_model, resolve_model

BuildAgent = Callable[[Any], Any]


def load_agent_module(agent_dir: Path) -> ModuleType:
    path = Path(agent_dir) / "agent.py"
    if not path.exists():
        raise KitConfigError(f"agent.py not found in {agent_dir}")
    sys.path.insert(0, str(Path(agent_dir).resolve()))  # lets agent.py import sibling modules such as tools.py
    spec = importlib.util.spec_from_file_location(f"agent_{Path(agent_dir).name.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "build_agent", None)):
        raise KitConfigError(f"{path} must define build_agent(model)")
    return module


def _error(type_: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"type": type_, "message": message[:1000], **extra}


def handle(
    payload: Any,
    *,
    build_agent: BuildAgent,
    agent_name: str,
    session_id: str | None = None,
    env: Mapping[str, str] = os.environ,
    model_factory: Callable[[Any], Any] = make_bedrock_model,
    write: Callable[[str], Any] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip():
        return {"output": None, "usage": None, "error": _error("BadRequest", "payload must be a JSON object with a non-empty 'prompt' string")}
    try:
        choice = resolve_model(payload.get("model"), env)
    except ModelNotAllowed as exc:
        return {"output": None, "usage": None, "error": _error("ModelNotAllowed", str(exc), allowed=exc.allowed)}
    except KitConfigError as exc:
        return {"output": None, "usage": None, "error": _error("ServerMisconfigured", str(exc))}

    agent = result = output = error = None
    start = clock()
    try:
        agent = build_agent(model_factory(choice))  # fresh Agent per request: Strands metrics accumulate per instance
        result = agent(payload["prompt"])
        output = str(result)
    except Exception as exc:  # noqa: BLE001 - every failure must still report usage (spec §3)
        error = _error(type(exc).__name__, str(exc))
    latency_ms = int((clock() - start) * 1000)

    metrics = getattr(result, "metrics", None) or getattr(agent, "event_loop_metrics", None)
    usage = usage_from_metrics(choice.alias, metrics, latency_ms)
    emit(
        emf_record(
            agent_name,
            usage,
            errors=1 if error else 0,
            project=env.get("PROJECT") or DEFAULT_PROJECT,  # deployment prefix, injected by CDK
            run_id=payload.get("run_id"),
            case_id=payload.get("case_id"),
            session_id=session_id,
        ),
        write=write,
    )
    return {"output": output, "usage": usage.to_dict(), "error": error}


def create_app(agent_dir: Path, agent_name: str):
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    module = load_agent_module(agent_dir)
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload, context):  # parameter must be named "context" for the SDK to inject it
        return handle(payload, build_agent=module.build_agent, agent_name=agent_name, session_id=getattr(context, "session_id", None))

    return app


def main() -> None:
    agent_dir = Path(os.environ.get("AGENT_DIR", "/app/agent"))
    agent_name = os.environ.get("AGENT_NAME", agent_dir.name)
    app = create_app(agent_dir, agent_name)
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
