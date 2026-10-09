"""G9 portability, G10 teardown, G11 billing reconciliation (spec §6)."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from template_core.config import load_config
from template_core.costs import ReconRow, billed_costs, completed_days_window, live_costs, reconcile
from template_core.deployment import DeploymentNotFound, load_deployment
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


def second_prefix_problem(main_prefix: str, prefix2: str) -> str | None:
    return "second prefix collides with main prefix" if prefix2 == main_prefix else None


def existing_stacks(cfn, prefix: str) -> list[str]:
    """Live (not DELETE_COMPLETE) CloudFormation stacks whose name starts with '<prefix>-'."""
    names, token = [], None
    while True:
        resp = cfn.list_stacks(**({"NextToken": token} if token else {}))
        names += [s["StackName"] for s in resp.get("StackSummaries", [])
                  if s.get("StackStatus") != "DELETE_COMPLETE" and s["StackName"].startswith(f"{prefix}-")]
        token = resp.get("NextToken")
        if not token:
            return names


def split_leftovers(arns: list[str], bucket: str | None, prefix: str | None = None) -> tuple[list[str], list[str]]:
    """(retained, leftovers): the expected results bucket's ARN (any partition) counts as retained; with `prefix`
    (removal: retain) so do results buckets that earlier runs of this prefix retained (`<prefix>-platform-results<id>`)."""
    patterns = []
    if bucket:
        patterns.append(re.compile(rf"^arn:[^:]+:s3:::{re.escape(bucket)}$"))
    if prefix:
        patterns.append(re.compile(rf"^arn:[^:]+:s3:::{re.escape(prefix.lower())}-platform-results[a-z0-9]+-[a-z0-9]+$"))
    retained = [a for a in arns if any(p.match(a) for p in patterns)]
    return retained, [a for a in arns if a not in retained]


def tagged_in_regions(session, regions: list[str], prefix: str) -> list[str]:
    seen: dict[str, None] = {}
    for region in regions:
        for arn in tagged_resources(session.client("resourcegroupstaggingapi", region_name=region), prefix):
            seen[arn] = None
    return list(seen)


def g11_checks(rows: list[ReconRow], cfg, message: str = "") -> list[Check]:
    checks, compared = [], 0
    for r in rows:
        name = f"{r.agent} × {r.model}"
        if r.within is not None:
            compared += 1
            checks.append(Check(f"{name} within ±10%", r.within,
                                f"estimate {format_usd(r.estimate)} billed {format_usd(r.billed)} delta {r.delta_pct:+.1f}%"))
        elif cfg.models[r.model].billing_tag and r.estimate and r.billed is None:
            checks.append(Check(f"{name}: no billed data for taggable pair", False, f"estimate {format_usd(r.estimate)}, {r.note}"))
        elif cfg.models[r.model].billing_tag and r.estimate == 0 and r.billed:
            checks.append(Check(f"{name}: billed without metered usage", False,
                                f"billed {format_usd(r.billed)} but the kit metered no tokens ({r.note})"))
        else:
            checks.append(Check(name, True, f"not compared: {r.note}"))
    checks.append(Check("at least one pair compared", compared > 0, f"{compared} compared"))
    if "Warning" in message:
        checks.append(Check("billing data caveat", False, message, warning=True))
    return checks


G11_DAYS_ENV = "TMPL_G11_DAYS"


def g11_days(env: Mapping[str, str] = os.environ) -> int:
    """Window length for G11 in whole days: TMPL_G11_DAYS, default 3."""
    raw = env.get(G11_DAYS_ENV) or "3"
    try:
        days = int(raw)
    except ValueError:
        days = 0
    if days < 1:
        raise ValueError(f"{G11_DAYS_ENV} must be a positive whole number of days, got {raw!r}")
    return days


def g11_window(today: date, days: int = 3) -> tuple[date, date, datetime, datetime]:
    """`days` whole completed UTC days ending at midnight UTC today (exclusive), as scripts/reconcile_costs.py."""
    return completed_days_window(today, days)


def _account(ctx: GateContext) -> str:
    return ctx.state.get("account") or ctx.client("sts").get_caller_identity()["Account"]


@gate("g9", "Portability")
def g9(ctx: GateContext) -> list[Check]:
    infra = ctx.repo_root / "infra"
    fake = ctx.run(
        ["npx", "cdk", "synth", "--quiet", "-c", "account=123456789012", "-c", "region=eu-west-1", "-c", f"prefix={second_prefix(ctx.cfg.prefix)}"],
        cwd=infra, env={**os.environ, "CDK_DEFAULT_ACCOUNT": "123456789012", "CDK_DEFAULT_REGION": "eu-west-1"},
    )
    checks = [command_check("synth for another account and region", fake)]

    prefix2 = second_prefix(ctx.cfg.prefix)
    problem = second_prefix_problem(ctx.cfg.prefix, prefix2)
    if problem:
        return [*checks, Check("second prefix is safe", False, problem)]
    existing = existing_stacks(ctx.client("cloudformation"), prefix2)
    if existing:
        return [*checks, Check("second prefix is unused", False, f"stacks for {prefix2} already exist; refusing to deploy over them: {', '.join(existing)}")]
    account = _account(ctx)
    if not ctx.confirm(f"Deploy a temporary second copy '{prefix2}' into account {account} ({ctx.cfg.region})?"):
        return [*checks, Check("second copy deploy confirmed", False, "declined")]
    ctx.state["second_prefix"] = prefix2  # recorded before deploying: G10 then cleans up even a partial deploy
    deploy = ctx.cdk("deploy", "--all", "--require-approval", "never", context={"prefix": prefix2, "removal": "destroy"})
    checks.append(command_check(f"deploy a second copy with prefix '{prefix2}'", deploy))
    if deploy.returncode == 0:
        matrix, _ = matrix_checks(load_config(prefix=prefix2, removal="destroy"), ctx.session, label=f"{prefix2}: ")
        checks += matrix

    hits = scan_for_account(ctx.repo_root, _account(ctx))
    checks.append(Check("account id absent from source", not hits, ", ".join(hits) or "clean"))
    return checks


@gate("g10", "Teardown")
def g10(ctx: GateContext) -> list[Check]:
    cfg = ctx.cfg
    prefix2 = ctx.state.get("second_prefix")
    if prefix2 != second_prefix(cfg.prefix):
        prefix2 = None
    retain = cfg.removal == "retain"
    targets = f"{cfg.prefix}{' and ' + prefix2 if prefix2 else ''}"
    if not ctx.confirm(f"Destroy stacks for prefixes {targets} in account {_account(ctx)} ({cfg.region})? "
                       f"Results bucket is {'retained' if retain else 'deleted'}."):
        return [Check("teardown confirmed", False, "declined")]
    try:
        bucket = load_deployment(cfg, ctx.session).results_bucket
    except DeploymentNotFound:
        bucket = None
    ctx.state["results_bucket"] = bucket

    checks: list[Check] = []
    if prefix2:
        checks.append(command_check(f"destroy '{prefix2}'", ctx.cdk("destroy", "--all", "--force", context={"prefix": prefix2, "removal": "destroy"})))
    checks.append(command_check(f"destroy '{cfg.prefix}'", ctx.cdk("destroy", "--all", "--force", context={"prefix": cfg.prefix, "removal": cfg.removal})))

    regions = list(dict.fromkeys([cfg.region, "us-east-1"]))
    deadline = time.monotonic() + 300
    while True:
        retained, leftovers = split_leftovers(tagged_in_regions(ctx.session, regions, cfg.prefix), bucket if retain else None,
                                             cfg.prefix if retain else None)
        left2 = tagged_in_regions(ctx.session, regions, prefix2) if prefix2 else []
        if (not leftovers and not left2) or time.monotonic() >= deadline:
            break
        time.sleep(30)
    checks.append(Check(f"nothing left tagged project={cfg.prefix}", not leftovers, ", ".join(leftovers) or "clean"))
    if retained:
        checks.append(Check("results bucket(s) retained (removal: retain, incl. earlier runs)", True, ", ".join(retained)))
    if prefix2:
        checks.append(Check(f"nothing left tagged project={prefix2}", not left2, ", ".join(left2) or "clean"))

    before = ctx.state.get("other_runtimes")
    after = other_runtimes(ctx.client("bedrock-agentcore-control"), cfg.prefix)
    checks.append(Check("other runtimes unchanged", before is not None and after == before, f"before={before} after={after}"))
    return checks


@gate("g11", "Billing reconciliation")
def g11(ctx: GateContext) -> list[Check]:
    start_day, end_day, start, end = g11_window(datetime.now(UTC).date(), g11_days())
    live = live_costs(ctx.client("cloudwatch"), ctx.cfg, ctx.prices, start, end)
    billed = billed_costs(ctx.client("ce", "us-east-1"), ctx.cfg, start_day, end_day)
    checks = [Check("billed data available", billed.status == "ok", billed.message)]
    if billed.status != "ok":
        return checks
    checks += g11_checks(reconcile(live, billed, ctx.cfg), ctx.cfg, billed.message)
    checks.append(Check("AgentCore compute carries the agent tag (spec §8 item 3)", bool(billed.agent_compute),
                        ", ".join(f"{a}: {format_usd(v)}" for a, v in billed.agent_compute.items()) or "no agent-tagged compute cost",
                        warning=not billed.agent_compute))
    return checks
