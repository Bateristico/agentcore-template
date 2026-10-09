"""Shared state for the Streamlit pages. Tests inject a Context via st.session_state[OVERRIDE_KEY]."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import boto3
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError

from bench.judge import Judge
from bench.store import RunStore
from template_core.agentcore import AgentClient, InvokeResult
from template_core.config import DeployConfig, load_config
from template_core.deployment import Deployment, DeploymentNotFound, load_deployment
from template_core.pricing import PriceTable, format_usd, load_pricing

OVERRIDE_KEY = "_ctx_override"


@dataclass
class Context:
    cfg: DeployConfig
    prices: PriceTable
    profile: str | None
    deployment: Deployment | None
    deployment_error: str | None
    client: AgentClient | None
    judge: Judge | None
    store: RunStore
    cloudwatch: Any
    cost_explorer: Any


def parse_profile(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--profile" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1]
    return None


def build_context(profile: str | None) -> Context:
    cfg, prices = load_config(), load_pricing()
    session = boto3.Session(profile_name=profile, region_name=cfg.region)
    try:
        deployment, error = load_deployment(cfg, session), None
    except (DeploymentNotFound, ClientError, BotoCoreError) as exc:
        deployment, error = None, str(exc)
    client = AgentClient.from_session(deployment, session) if deployment else None
    judge = Judge(cfg.judge, deployment.judge_profile, session.client("bedrock-runtime", region_name=cfg.region)) if deployment else None
    store = RunStore(s3=session.client("s3"), bucket=deployment.results_bucket) if deployment else RunStore()
    return Context(cfg, prices, profile, deployment, error, client, judge, store,
                   session.client("cloudwatch", region_name=cfg.region), session.client("ce", region_name="us-east-1"))


@st.cache_resource(show_spinner="Connecting to AWS…")
def _cached_context(profile: str | None) -> Context:
    return build_context(profile)


def get_context() -> Context:
    override = st.session_state.get(OVERRIDE_KEY)
    return override if override is not None else _cached_context(parse_profile(sys.argv[1:]))


def result_metrics(result: InvokeResult, prices: PriceTable) -> dict[str, str]:
    u = result.usage
    cost = prices.try_model_cost(u.model, u) if u else None
    return {
        "model": result.model or "-",
        "tokens in": str(u.input_tokens) if u else "-",
        "tokens out": str(u.output_tokens) if u else "-",
        "tool calls": str(u.tool_calls) if u else "-",
        "latency": f"{u.latency_ms} ms" if u else f"{result.client_latency_ms} ms",
        "cost": format_usd(cost.total if cost else None) if u else "-",
    }
