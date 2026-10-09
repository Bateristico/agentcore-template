"""Calls deployed agents through the AgentCore data plane (spec §3 invocation contract)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from agentkit.metering import Usage
from template_core.deployment import Deployment

THROTTLE_TYPES = frozenset(
    {"ThrottlingException", "ModelThrottledException", "ServiceQuotaExceededException", "TooManyRequestsException", "ServiceUnavailableException"}
)


@dataclass(frozen=True)
class InvokeResult:
    agent: str
    model: str | None
    session_id: str
    output: str | None
    usage: Usage | None
    error: dict[str, Any] | None
    client_latency_ms: int

    @property
    def ok(self) -> bool:
        return self.error is None and self.output is not None

    @property
    def throttled(self) -> bool:
        return bool(self.error) and self.error.get("type") in THROTTLE_TYPES


def new_session_id() -> str:
    return f"tmpl-{uuid.uuid4()}"  # 41 chars; AgentCore requires >= 33


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def parse_response(agent: str, model: str | None, session_id: str, body: bytes, latency_ms: int) -> InvokeResult:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        error = {"type": "BadResponse", "message": f"runtime returned an unexpected body: {body[:200]!r}"}
        return InvokeResult(agent, model, session_id, None, None, error, latency_ms)
    usage = Usage.from_dict(data["usage"]) if isinstance(data.get("usage"), dict) else None
    return InvokeResult(agent, usage.model if usage else model, session_id, data.get("output"), usage, data.get("error"), latency_ms)


class AgentClient:
    def __init__(self, deployment: Deployment, client: Any):
        self.deployment = deployment
        self._client = client

    @classmethod
    def from_session(cls, deployment: Deployment, session, read_timeout: int = 300) -> AgentClient:
        config = Config(read_timeout=read_timeout, retries={"max_attempts": 2, "mode": "standard"})
        return cls(deployment, session.client("bedrock-agentcore", region_name=session.region_name, config=config))

    def invoke(
        self,
        agent: str,
        prompt: str,
        *,
        model: str | None = None,
        run_id: str | None = None,
        case_id: str | None = None,
        session_id: str | None = None,
    ) -> InvokeResult:
        endpoint = self.deployment.agents.get(agent)
        if endpoint is None:
            raise KeyError(f"agent '{agent}' is not deployed (deployed: {', '.join(self.deployment.agents)})")
        sid = session_id or new_session_id()
        expected_model = model or endpoint.default_model
        payload = {"prompt": prompt, **{k: v for k, v in {"model": model, "run_id": run_id, "case_id": case_id}.items() if v}}
        start = time.perf_counter()
        try:
            resp = self._client.invoke_agent_runtime(
                agentRuntimeArn=endpoint.runtime_arn,
                runtimeSessionId=sid,
                payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                contentType="application/json",
                accept="application/json",
            )
            body = resp["response"].read()
        except ClientError as exc:
            err = exc.response.get("Error", {})
            error = {"type": err.get("Code", "ClientError"), "message": err.get("Message", str(exc))}
            return InvokeResult(agent, expected_model, sid, None, None, error, _ms(start))
        except BotoCoreError as exc:
            return InvokeResult(agent, expected_model, sid, None, None, {"type": type(exc).__name__, "message": str(exc)}, _ms(start))
        return parse_response(agent, expected_model, sid, body, _ms(start))
