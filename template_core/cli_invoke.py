"""`uv run invoke --agent tools-agent --model haiku-4-5 --prompt "..." --profile <profile>`"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from template_core.agentcore import AgentClient, InvokeResult
from template_core.config import DEFAULT_CONFIG, ConfigError, load_config
from template_core.deployment import DeploymentNotFound, load_deployment
from template_core.pricing import DEFAULT_PRICING, PriceTable, format_usd, load_pricing


def format_result(result: InvokeResult, prices: PriceTable) -> str:
    lines: list[str] = []
    if result.output is not None:
        lines += [result.output, ""]
    if result.error:
        lines.append(f"ERROR {result.error.get('type')}: {result.error.get('message')}")
    if result.usage:
        u = result.usage
        cost = prices.try_model_cost(u.model, u)
        lines.append(
            f"model={u.model} in={u.input_tokens} out={u.output_tokens} cache_read={u.cache_read_tokens} "
            f"cache_write={u.cache_write_tokens} cycles={u.cycles} tools={u.tool_calls} latency={u.latency_ms}ms"
        )
        lines.append(f"cost={format_usd(cost.total if cost else None)}")
    lines.append(f"session={result.session_id}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="invoke", description="Call a deployed agent once and show usage and cost")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--model", help="model alias (default: the agent's default)")
    ap.add_argument("--profile", help="AWS CLI profile")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--pricing", default=str(DEFAULT_PRICING))
    ap.add_argument("--json", action="store_true", help="print the raw result as JSON")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
        prices = load_pricing(args.pricing)
        session = boto3.Session(profile_name=args.profile, region_name=cfg.region)
        client = AgentClient.from_session(load_deployment(cfg, session), session)
        result = client.invoke(args.agent, args.prompt, model=args.model)
    except (DeploymentNotFound, ConfigError, KeyError, NoCredentialsError, ClientError, BotoCoreError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"invoke: {msg}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({**result.__dict__, "usage": result.usage.to_dict() if result.usage else None}, ensure_ascii=False, indent=2))
    else:
        print(format_result(result, prices))
    return 0 if result.ok else 1
