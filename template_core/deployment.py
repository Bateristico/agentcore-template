"""Reads what is deployed from CloudFormation stack outputs (platform and per-agent stacks)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from botocore.exceptions import ClientError

from template_core.config import DeployConfig


class DeploymentNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentEndpoint:
    name: str
    runtime_arn: str
    runtime_id: str
    model_profiles: dict[str, str]
    default_model: str


@dataclass(frozen=True)
class Deployment:
    prefix: str
    results_bucket: str
    judge_model: str
    judge_profile: str
    agents: dict[str, AgentEndpoint]


def _outputs(cfn, stack_name: str) -> dict[str, str]:
    try:
        stacks = cfn.describe_stacks(StackName=stack_name)["Stacks"]
    except ClientError as exc:
        if "does not exist" in str(exc):
            raise DeploymentNotFound(f"stack {stack_name} is not deployed; run `npx cdk deploy --all` in infra/") from exc
        raise
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def load_deployment(cfg: DeployConfig, session) -> Deployment:
    cfn = session.client("cloudformation", region_name=cfg.region)
    platform = _outputs(cfn, cfg.platform_stack_name)
    agents = {}
    for name in cfg.agents:
        out = _outputs(cfn, cfg.agent_stack_name(name))
        agents[name] = AgentEndpoint(
            name=name,
            runtime_arn=out["RuntimeArn"],
            runtime_id=out["RuntimeId"],
            model_profiles=json.loads(out["ModelProfiles"]),
            default_model=out["DefaultModel"],
        )
    return Deployment(
        prefix=cfg.prefix,
        results_bucket=platform["ResultsBucketName"],
        judge_model=platform["JudgeModel"],
        judge_profile=platform["JudgeProfileArn"],
        agents=agents,
    )
