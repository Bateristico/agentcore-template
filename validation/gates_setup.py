"""G0 preflight, G1 offline tests, G2 local smoke (spec §6)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

from botocore.exceptions import BotoCoreError, ClientError

from template_core.costs import cost_tag_state
from template_core.pricing import format_usd
from validation.framework import Check, GateContext, command_check, gate, kill_tree, new_session_kwargs
from validation.spend import validation_spend_estimate

SMOKE_PROMPT = "Reply with the single word: pong"


@gate("g0", "Preflight")
def g0(ctx: GateContext) -> list[Check]:
    checks: list[Check] = []
    ident = ctx.client("sts").get_caller_identity()
    account = ident["Account"]
    ctx.state["account"] = account
    if ctx.cfg.account:
        checks.append(Check("account matches deploy.yaml", account == ctx.cfg.account, f"credentials {account}, pinned {ctx.cfg.account}"))
    else:
        ok = ctx.confirm(f"Validate and deploy into account {account} ({ident['Arn']}) in {ctx.cfg.region}?")
        checks.append(Check("target account confirmed", ok, f"{account} / {ctx.cfg.region}"))
    if not checks[-1].ok:
        return checks

    try:
        status = ctx.client("cloudformation").describe_stacks(StackName="CDKToolkit")["Stacks"][0]["StackStatus"]
        checks.append(Check("CDK bootstrap", status.endswith("_COMPLETE") and "ROLLBACK" not in status, status))
    except ClientError as exc:
        checks.append(Check("CDK bootstrap", False, f"run `npx cdk bootstrap aws://{account}/{ctx.cfg.region}` ({exc})"))

    runtime = ctx.client("bedrock-runtime")
    for alias, model in ctx.cfg.models.items():
        try:
            runtime.converse(modelId=model.profile, messages=[{"role": "user", "content": [{"text": SMOKE_PROMPT}]}],
                             inferenceConfig={"maxTokens": 16})
            checks.append(Check(f"model access: {alias}", True, model.profile))
        except (ClientError, BotoCoreError) as exc:
            checks.append(Check(f"model access: {alias}", False, f"{model.profile}: {exc}"))

    missing = ctx.prices.missing(ctx.cfg.models)
    checks.append(Check("every model has a price", not missing, ", ".join(missing) or "all priced"))

    tags = cost_tag_state(ctx.client("ce", "us-east-1"), ["project", "agent", "model"])
    checks.append(Check("cost allocation tags active", tags == "active",
                        f"{tags} (needed only for billed cost / G11; activate in the payer account)", warning=tags != "active"))

    estimate = validation_spend_estimate(ctx.cfg, ctx.prices)
    checks.append(Check("validation spend under $5", estimate < 5, f"estimated {format_usd(estimate)}"))
    return checks


@gate("g1", "Offline tests")
def g1(ctx: GateContext) -> list[Check]:
    infra = ctx.repo_root / "infra"
    synth_env = {**os.environ, "CDK_DEFAULT_ACCOUNT": "123456789012", "CDK_DEFAULT_REGION": ctx.cfg.region}
    return [
        command_check("pytest", ctx.run(["uv", "run", "pytest", "-q"])),
        command_check("ruff", ctx.run(["uv", "run", "ruff", "check", "."])),
        command_check("jest (CDK assertions, cdk-nag)", ctx.run(["npm", "test"], cwd=infra)),
        command_check("tsc", ctx.run(["npm", "run", "build"], cwd=infra)),
        command_check("cdk synth with cdk-nag", ctx.run(["npx", "cdk", "synth", "--quiet"], cwd=infra, env=synth_env)),
    ]


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for_ping(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def post_json(port: int, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/invocations", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


@gate("g2", "Local smoke")
def g2(ctx: GateContext) -> list[Check]:
    checks: list[Check] = []
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    for i, (name, agent) in enumerate(ctx.cfg.agents.items()):
        port = 8090 + i
        folder = ctx.repo_root / "agents" / name
        env = {
            **os.environ, "AGENT_DIR": str(folder), "AGENT_NAME": name, "HOST": "127.0.0.1", "PORT": str(port),
            "MODEL_PROFILES": json.dumps({agent.default: ctx.cfg.models[agent.default].profile}),
            "DEFAULT_MODEL": agent.default, "AWS_REGION": ctx.cfg.region,
        }
        if ctx.profile:
            env["AWS_PROFILE"] = ctx.profile
        log_path = ctx.out_dir / f"g2-{name}.log"
        if port_in_use(port):
            checks.append(Check(f"{name}: server starts", False, f"port {port} already in use"))
            continue
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                [shutil.which("uv") or "uv", "run", "--with-requirements", str(folder / "requirements.txt"), "python", "-m", "agentkit.server"],
                cwd=ctx.repo_root, env=env, stdout=log, stderr=subprocess.STDOUT,
                **new_session_kwargs(),
            )
            try:
                if not wait_for_ping(port, 120):
                    checks.append(Check(f"{name}: server starts", False, f"no /ping within 120 s; see {log_path}"))
                    continue
                status, body = post_json(port, {"prompt": SMOKE_PROMPT})
                usage = body.get("usage") or {}
                ok = (
                    status == 200 and body.get("error") is None and bool((body.get("output") or "").strip())
                    and all((usage.get(k) or 0) > 0 for k in ("input_tokens", "output_tokens", "latency_ms"))
                )
                checks.append(Check(f"{name}: local /invocations on {agent.default}", ok, json.dumps(body, ensure_ascii=False)[:500]))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                checks.append(Check(f"{name}: local /invocations", False, f"{type(exc).__name__}: {exc}; see {log_path}"))
            finally:
                kill_tree(proc.pid)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return checks
