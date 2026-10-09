"""Checks whether a Bedrock model works for this template: Converse via its system
inference profile, and Converse via a temporary application inference profile (spec §8 item 2)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError

PROBE_MAX_TOKENS = 16  # some models (e.g. kimi-k3) reject smaller limits
PROBE_MESSAGES = [{"role": "user", "content": [{"text": "Reply with the single word: ok"}]}]


@dataclass(frozen=True)
class ProbeResult:
    profile_id: str
    converse_ok: bool
    converse_error: str | None
    app_profile_ok: bool | None  # None = not tried because Converse failed
    app_profile_error: str | None


def system_profile_arn(region: str, account: str, profile_id: str) -> str:
    return f"arn:aws:bedrock:{region}:{account}:inference-profile/{profile_id}"


def _describe(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        return f"{err.get('Code', 'ClientError')}: {err.get('Message', '')}"
    return f"{type(exc).__name__}: {exc}"


def _converse(runtime, model_id: str) -> None:
    runtime.converse(modelId=model_id, messages=PROBE_MESSAGES, inferenceConfig={"maxTokens": PROBE_MAX_TOKENS})


def probe_model(bedrock, runtime, *, profile_id: str, region: str, account: str, name_prefix: str = "probe") -> ProbeResult:
    try:
        _converse(runtime, profile_id)
    except (ClientError, BotoCoreError) as exc:
        return ProbeResult(profile_id, False, _describe(exc), None, None)

    try:
        created = bedrock.create_inference_profile(
            inferenceProfileName=f"{name_prefix}-{secrets.token_hex(4)}",
            description=f"temporary probe for {profile_id}",
            modelSource={"copyFrom": system_profile_arn(region, account, profile_id)},
            tags=[{"key": "project", "value": name_prefix}],
        )
    except (ClientError, BotoCoreError) as exc:
        return ProbeResult(profile_id, True, None, False, _describe(exc))

    arn = created["inferenceProfileArn"]
    try:
        _converse(runtime, arn)
        return ProbeResult(profile_id, True, None, True, None)
    except (ClientError, BotoCoreError) as exc:
        return ProbeResult(profile_id, True, None, False, _describe(exc))
    finally:
        bedrock.delete_inference_profile(inferenceProfileIdentifier=arn)


def format_probe_table(results: list[ProbeResult]) -> str:
    def yn(v: bool | None) -> str:
        return "n/a" if v is None else ("yes" if v else "no")

    lines = [
        "| profile | converse | application profile | error |",
        "|---|---|---|---|",
    ]
    for r in results:
        error = r.converse_error or r.app_profile_error or ""
        lines.append(f"| {r.profile_id} | {yn(r.converse_ok)} | {yn(r.app_profile_ok)} | {error} |")
    return "\n".join(lines)
