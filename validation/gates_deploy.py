"""G3 deploy, G4 deployed matrix, G5 metering accuracy (spec §6)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from agentkit.metering import NAMESPACE
from template_core.agentcore import AgentClient
from template_core.config import DeployConfig
from template_core.costs import sum_metrics
from template_core.deployment import load_deployment
from validation.framework import Check, GateContext, command_check, gate, within

PONG = "Reply with the single word: pong"


def other_runtimes(control, prefix: str) -> dict[str, str]:
    runtimes: dict[str, str] = {}
    token = None
    while True:
        resp = control.list_agent_runtimes(**({"nextToken": token} if token else {}))
        for r in resp.get("agentRuntimes", []):
            if not r["agentRuntimeName"].startswith(f"{prefix}_"):
                runtimes[r["agentRuntimeName"]] = str(r["agentRuntimeVersion"])
        token = resp.get("nextToken")
        if not token:
            return runtimes


def wait_runtime_ready(control, runtime_id: str, timeout: float = 900, sleep=time.sleep) -> str:
    deadline = time.monotonic() + timeout
    while True:
        status = control.get_agent_runtime(agentRuntimeId=runtime_id)["status"]
        if status == "READY" or status.endswith("FAILED") or time.monotonic() >= deadline:
            return status
        sleep(15)


@gate("g3", "Deploy")
def g3(ctx: GateContext) -> list[Check]:
    control = ctx.client("bedrock-agentcore-control")
    before = other_runtimes(control, ctx.cfg.prefix)
    ctx.state["other_runtimes"] = before
    diff = ctx.cdk("diff", "--all")
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    (ctx.out_dir / "g3-cdk-diff.txt").write_text(diff.stdout + diff.stderr, encoding="utf-8")
    if not ctx.confirm(f"Deploy all stacks (diff in {ctx.out_dir / 'g3-cdk-diff.txt'})?"):
        return [Check("deploy confirmed", False, "declined")]
    deploy = ctx.cdk("deploy", "--all", "--require-approval", "never")
    checks = [command_check("cdk deploy --all", deploy)]
    if deploy.returncode != 0:
        return checks

    cfn = ctx.client("cloudformation")
    for stack in [ctx.cfg.platform_stack_name, *(ctx.cfg.agent_stack_name(a) for a in ctx.cfg.agents)]:
        status = cfn.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
        checks.append(Check(f"stack {stack}", status in ("CREATE_COMPLETE", "UPDATE_COMPLETE"), status))
    for name, endpoint in load_deployment(ctx.cfg, ctx.session).agents.items():
        status = wait_runtime_ready(control, endpoint.runtime_id)
        checks.append(Check(f"runtime {name} READY", status == "READY", status))
    after = other_runtimes(control, ctx.cfg.prefix)
    checks.append(Check("other runtimes unchanged", after == before, f"before={before} after={after}"))
    return checks


def matrix_checks(cfg: DeployConfig, session, label: str = "") -> tuple[list[Check], dict]:
    deployment = load_deployment(cfg, session)
    client = AgentClient.from_session(deployment, session)
    checks: list[Check] = []
    start = datetime.now(UTC)
    for name, agent in cfg.agents.items():
        for model in agent.models:
            r = client.invoke(name, PONG, model=model, run_id=f"validate-{cfg.prefix}")
            ok = r.ok and r.usage is not None and r.usage.input_tokens > 0 and r.usage.output_tokens > 0 and r.usage.model == model
            detail = json.dumps(r.error) if r.error else f"model={r.usage.model if r.usage else None} in={r.usage.input_tokens if r.usage else 0}"
            checks.append(Check(f"{label}{name} × {model}", ok, detail))
        disallowed = next((m for m in cfg.models if m not in agent.models), "not-a-model")
        r = client.invoke(name, PONG, model=disallowed)
        checks.append(Check(f"{label}{name} rejects '{disallowed}'", (r.error or {}).get("type") == "ModelNotAllowed", json.dumps(r.error)))
    profiles = {
        f"{name}|{model}": deployment.agents[name].model_profiles[model]
        for name, agent in cfg.agents.items()
        for model in agent.models
        if cfg.models[model].billing_tag
    }
    return checks, {"start": start.isoformat(), "end": datetime.now(UTC).isoformat(), "profiles": profiles}


def profile_dimension_values(arn: str) -> list[str]:
    return [arn.rsplit("/", 1)[-1], arn]


def wait_for_profile_metrics(cw, arns: Iterable[str], timeout: float = 600, sleep=time.sleep, clock=time.monotonic) -> tuple[dict[str, str], str | None]:
    pending, found, form = set(arns), {}, None
    deadline = clock() + timeout
    while pending:
        for arn in list(pending):
            for value in profile_dimension_values(arn):
                resp = cw.list_metrics(Namespace="AWS/Bedrock", MetricName="Invocations", Dimensions=[{"Name": "ModelId", "Value": value}])
                if resp.get("Metrics"):
                    found[arn] = value
                    form = "profile-arn" if value == arn else "profile-id"
                    pending.discard(arn)
                    break
        if not pending or clock() >= deadline:
            break
        sleep(30)
    return found, form


@gate("g4", "Deployed matrix")
def g4(ctx: GateContext) -> list[Check]:
    checks, window = matrix_checks(ctx.cfg, ctx.session)
    ctx.state["g4"] = window
    found, form = wait_for_profile_metrics(ctx.client("cloudwatch"), window["profiles"].values())
    ctx.state["model_id_dimension"] = form
    for key, arn in window["profiles"].items():
        checks.append(Check(f"AWS/Bedrock metrics via profile {key}", arn in found, f"ModelId={found.get(arn, 'not found after 10 min')}"))
    return checks


def _sums(cw, namespace: str, metrics: dict[str, str], dims: list[dict[str, str]], start, end) -> dict[str, float | None]:
    return sum_metrics(cw, {k: {"Namespace": namespace, "MetricName": m, "Dimensions": dims} for k, m in metrics.items()}, start, end)


@gate("g5", "Metering accuracy")
def g5(ctx: GateContext) -> list[Check]:
    window = ctx.state.get("g4")
    if not window:
        return [Check("G4 window available", False, "run g4 first")]
    start = datetime.fromisoformat(window["start"]) - timedelta(minutes=2)
    end = datetime.fromisoformat(window["end"]) + timedelta(minutes=5)
    use_arn = ctx.state.get("model_id_dimension") == "profile-arn"
    cw = ctx.client("cloudwatch")
    deadline = time.monotonic() + 900
    while True:
        checks: list[Check] = []
        for key, arn in window["profiles"].items():
            agent, model = key.split("|")
            ours = _sums(cw, NAMESPACE, {"in": "InputTokens", "out": "OutputTokens"},
                         [{"Name": "Agent", "Value": agent}, {"Name": "Model", "Value": model}], start, end)
            theirs = _sums(cw, "AWS/Bedrock", {"in": "InputTokenCount", "out": "OutputTokenCount"},
                           [{"Name": "ModelId", "Value": arn if use_arn else arn.rsplit("/", 1)[-1]}], start, end)
            for k in ("in", "out"):
                ok = ours[k] is not None and theirs[k] is not None and within(ours[k], theirs[k], 0.02)
                checks.append(Check(f"{key} {k}put tokens within ±2%", ok, f"kit={ours[k]} bedrock={theirs[k]}"))
        if all(c.ok for c in checks) or time.monotonic() >= deadline:
            return checks
        time.sleep(60)  # EMF and Bedrock metrics arrive within a few minutes
