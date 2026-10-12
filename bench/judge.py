"""LLM-as-judge via Bedrock Converse on the judge's application profile (spec §5 Judge)."""

from __future__ import annotations

import json
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from agentkit.metering import Usage
from bench.records import Verdict
from bench.suite import Case
from template_core.config import DeployConfig

SYSTEM_PROMPT = "You are a strict evaluator of AI assistant answers. You are never told which system produced an answer."
_SCALE = (
    "5 = fully correct and complete; 4 = correct with minor issues; 3 = partially correct; "
    "2 = mostly wrong; 1 = wrong, off-topic or empty."
)

def build_prompt(case: Case, response: str) -> str:
    parts = [f"Grade the RESPONSE to the PROMPT on a 1-5 scale. {_SCALE}"]
    if case.expected:
        parts.append(f"<expected>\n{case.expected}\n</expected>")
    if case.rubric:
        parts.append(f"<rubric>\n{case.rubric}\n</rubric>")
    parts.append(f"<prompt>\n{case.prompt}\n</prompt>")
    parts.append(f"<response>\n{response}\n</response>")
    parts.append('Reply with only a JSON object: {"score": <integer 1-5>, "reason": "<one sentence>"}')
    return "\n\n".join(parts)


def parse_verdict(text: str) -> tuple[int, str]:
    """Return the first JSON object in ``text`` that carries a valid integer score 1-5."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        score = data.get("score")
        if isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 5:
            return score, str(data.get("reason", ""))[:500]
    raise ValueError("no JSON object with an integer score 1-5 in judge output")


def _response_text(resp: Any) -> str:
    """Concatenate text blocks; any odd shape yields an empty string instead of raising."""
    output = resp.get("output") if isinstance(resp, dict) else None
    message = output.get("message") if isinstance(output, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    blocks = content if isinstance(content, list) else []
    return "".join(b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))


def usage_from_converse(alias: str, resp: dict[str, Any]) -> Usage:
    u = resp.get("usage") or {}
    return Usage(
        alias,
        input_tokens=int(u.get("inputTokens") or 0),
        output_tokens=int(u.get("outputTokens") or 0),
        cache_read_tokens=int(u.get("cacheReadInputTokens") or 0),
        cache_write_tokens=int(u.get("cacheWriteInputTokens") or 0),
        latency_ms=int((resp.get("metrics") or {}).get("latencyMs") or 0),
    )


class Judge:
    def __init__(self, alias: str, model_id: str, client: Any):
        self.alias = alias
        self.model_id = model_id
        self._client = client

    def grade(self, case: Case, response: str) -> Verdict:
        total = Usage(self.alias)
        last_error = "judge not called"
        for _ in range(2):  # one retry on unparseable output (spec §5)
            try:
                resp = self._client.converse(
                    modelId=self.model_id,
                    system=[{"text": SYSTEM_PROMPT}],
                    messages=[{"role": "user", "content": [{"text": build_prompt(case, response)}]}],
                    inferenceConfig={"maxTokens": 300, "temperature": 0},
                )
            except ClientError as exc:
                err = exc.response.get("Error", {})
                return Verdict(None, None, total if total.input_tokens else None, f"{err.get('Code')}: {err.get('Message')}")
            except BotoCoreError as exc:
                return Verdict(None, None, total if total.input_tokens else None, f"{type(exc).__name__}: {exc}")
            total = total + usage_from_converse(self.alias, resp)
            text = _response_text(resp)
            try:
                score, reason = parse_verdict(text)
                return Verdict(score, reason, total, None)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = f"unparseable judge output: {exc}"
        return Verdict(None, None, total, last_error)


def bias_warnings(cfg: DeployConfig, judge_alias: str, candidates: list[str]) -> list[str]:
    if judge_alias not in cfg.models:
        return []
    family = cfg.models[judge_alias].family
    return [
        f"judge '{judge_alias}' and candidate '{alias}' are both {family} models; scores may favour it"
        for alias in candidates
        if alias != judge_alias and alias in cfg.models and cfg.models[alias].family == family
    ] + ([f"judge '{judge_alias}' is also a candidate; its own scores may be inflated"] if judge_alias in candidates else [])
