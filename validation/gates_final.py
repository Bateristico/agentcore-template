"""G9 portability, G10 teardown, G11 billing reconciliation (spec §6)."""

from __future__ import annotations

import os
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from template_core.config import load_config
from template_core.costs import billed_costs, live_costs, reconcile
from template_core.pricing import format_usd
from validation.framework import Check, GateContext, command_check, gate
from validation.gates_deploy import matrix_checks, other_runtimes

SOURCE_DIRS = ("agents", "bench", "config", "infra/bin", "infra/lib", "infra/test", "scripts", "template_core", "tests", "ui", "validation")
_SKIP_DIRS = {"node_modules", "cdk.out", "__pycache__", ".venv", "out"}
_EVIDENCE_DIR = re.compile(r"^\d{8}T\d{6}Z$")
_TEXT_SUFFIXES = {".py", ".ts", ".js", ".json", ".yaml", ".yml", ".md", ".txt", ".toml", ""}


def scan_for_account(repo_root: Path, account: str) -> list[str]:
    hits = []
    for top in SOURCE_DIRS:
        base = Path(repo_root) / top
        if not base.exists():
            continue
        for path in base.rglob("*"):
            rel_parts = path.relative_to(repo_root).parts
            if any(p in _SKIP_DIRS or _EVIDENCE_DIR.match(p) for p in rel_parts) or not path.is_file():
                continue
            if path.suffix not in _TEXT_SUFFIXES:
                continue
            if account in path.read_text(encoding="utf-8", errors="ignore"):
                hits.append("/".join(rel_parts))
    return sorted(hits)


def second_prefix(prefix: str) -> str:
    return f"{prefix[:11]}2"


def tagged_resources(tagging, prefix: str) -> list[str]:
    arns, token = [], ""
    while True:
        resp = tagging.get_resources(TagFilters=[{"Key": "project", "Values": [prefix]}], PaginationToken=token)
        arns += [r["ResourceARN"] for r in resp.get("ResourceTagMappingList", [])]
        token = resp.get("PaginationToken", "")
        if not token:
            return arns


def g11_window(today: date) -> tuple[date, date, datetime, datetime]:
    """Three whole completed UTC days ending at midnight UTC today (exclusive), as scripts/reconcile_costs.py."""
    end_day = today
    start_day = end_day - timedelta(days=3)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC)
    return start_day, end_day, start, end


@gate("g9", "Portability")
def g9(ctx: GateContext) -> list[Check]:
    infra = ctx.repo_root / "infra"
    fake = ctx.run(
        ["npx", "cdk", "synth", "--quiet", "-c", "account=123456789012", "-c", "region=eu-west-1", "-c", f"prefix={second_prefix(ctx.cfg.prefix)}"],
        cwd=infra, env={**os.environ, "CDK_DEFAULT_ACCOUNT": "123456789012", "CDK_DEFAULT_REGION": "eu-west-1"},
    )
    checks = [command_check("synth for another account and region", fake)]

    prefix2 = second_prefix(ctx.cfg.prefix)
    ctx.state["second_prefix"] = prefix2
    deploy = ctx.cdk("deploy", "--all", "--require-approval", "never", context={"prefix": prefix2, "removal": "destroy"})
    checks.append(command_check(f"deploy a second copy with prefix '{prefix2}'", deploy))
    if deploy.returncode == 0:
        matrix, _ = matrix_checks(load_config(prefix=prefix2, removal="destroy"), ctx.session, label=f"{prefix2}: ")
        checks += matrix

    account = ctx.state.get("account") or ctx.client("sts").get_caller_identity()["Account"]
    hits = scan_for_account(ctx.repo_root, account)
    checks.append(Check("account id absent from source", not hits, ", ".join(hits) or "clean"))
    return checks


@gate("g10", "Teardown")
def g10(ctx: GateContext) -> list[Check]:
    if not ctx.confirm("Destroy all template stacks?"):
        return [Check("teardown confirmed", False, "declined")]
    checks: list[Check] = []
    prefix2 = ctx.state.get("second_prefix")
    if prefix2:
        checks.append(command_check(f"destroy '{prefix2}'", ctx.cdk("destroy", "--all", "--force", context={"prefix": prefix2, "removal": "destroy"})))
    checks.append(command_check(f"destroy '{ctx.cfg.prefix}'", ctx.cdk("destroy", "--all", "--force")))

    tagging = ctx.client("resourcegroupstaggingapi")
    deadline = time.monotonic() + 300
    while True:
        left = tagged_resources(tagging, ctx.cfg.prefix)
        retained = [a for a in left if a.startswith("arn:aws:s3:::") and ctx.cfg.removal == "retain"]
        leftovers = [a for a in left if a not in retained]
        left2 = tagged_resources(tagging, prefix2) if prefix2 else []
        if (not leftovers and not left2) or time.monotonic() >= deadline:
            break
        time.sleep(30)
    checks.append(Check(f"nothing left tagged project={ctx.cfg.prefix}", not leftovers, ", ".join(leftovers) or "clean"))
    if retained:
        checks.append(Check("results bucket retained (removal: retain)", True, ", ".join(retained)))
    if prefix2:
        checks.append(Check(f"nothing left tagged project={prefix2}", not left2, ", ".join(left2) or "clean"))

    before = ctx.state.get("other_runtimes")
    after = other_runtimes(ctx.client("bedrock-agentcore-control"), ctx.cfg.prefix)
    checks.append(Check("other runtimes unchanged", before is not None and after == before, f"before={before} after={after}"))
    return checks


@gate("g11", "Billing reconciliation")
def g11(ctx: GateContext) -> list[Check]:
    start_day, end_day, start, end = g11_window(datetime.now(UTC).date())
    live = live_costs(ctx.client("cloudwatch"), ctx.cfg, ctx.prices, start, end)
    billed = billed_costs(ctx.client("ce", "us-east-1"), ctx.cfg, start_day, end_day)
    checks = [Check("billed data available", billed.status == "ok", billed.message)]
    if billed.status != "ok":
        return checks
    for r in reconcile(live, billed, ctx.cfg):
        if r.within is None:
            checks.append(Check(f"{r.agent} × {r.model}", True, f"not compared: {r.note}"))
        else:
            checks.append(Check(f"{r.agent} × {r.model} within ±10%", r.within,
                                f"estimate {format_usd(r.estimate)} billed {format_usd(r.billed)} delta {r.delta_pct:+.1f}%"))
    checks.append(Check("AgentCore compute carries the agent tag (spec §8 item 3)", bool(billed.agent_compute),
                        ", ".join(f"{a}: {format_usd(v)}" for a, v in billed.agent_compute.items()) or "no agent-tagged compute cost",
                        warning=not billed.agent_compute))
    return checks
