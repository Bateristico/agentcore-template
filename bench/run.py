"""Benchmark CLI (spec §5).

    uv run bench/run.py --agent tools-agent --suite basics --models haiku-4-5,sonnet-4-6 --repeats 3 --profile <profile>
    uv run bench/run.py report <run_id> [--reprice] --profile <profile>
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError

from bench.judge import Judge
from bench.orchestrate import execute_run, rerender
from bench.report import summary_table
from bench.runner import RunSpec, estimate_cost, new_run_id
from bench.store import RunStore
from bench.suite import load_suite
from template_core.agentcore import AgentClient
from template_core.config import ConfigError, load_config
from template_core.console import utf8_stdout
from template_core.deployment import DeploymentNotFound, load_deployment
from template_core.pricing import format_usd, load_pricing


def _print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    widths = {k: max(len(k), *(len(r[k]) for r in rows)) for k in rows[0]}
    print("  ".join(k.ljust(widths[k]) for k in rows[0]))
    for r in rows:
        print("  ".join(r[k].ljust(widths[k]) for k in r))


def _run_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="bench/run.py")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--models", help="comma-separated aliases (default: all models allowed for the agent)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    ap.add_argument("--profile")
    args = ap.parse_args(argv)
    if args.repeats < 1 or args.concurrency < 1:
        print("--repeats and --concurrency must be at least 1", file=sys.stderr)
        return 2

    cfg, prices = load_config(), load_pricing()
    if args.agent not in cfg.agents:
        print(f"unknown agent '{args.agent}'; configured: {', '.join(cfg.agents)}", file=sys.stderr)
        return 2
    allowed = cfg.agents[args.agent].models
    models = list(dict.fromkeys(args.models.split(","))) if args.models else list(allowed)
    bad = [m for m in models if m not in allowed]
    if bad:
        print(f"models not allowed for {args.agent}: {', '.join(bad)}; allowed: {', '.join(allowed)}", file=sys.stderr)
        return 2

    spec = RunSpec(new_run_id(args.agent), args.agent, load_suite(args.agent, args.suite), models, args.repeats, args.concurrency)
    judge_alias = None if args.no_judge else cfg.judge
    estimate = estimate_cost(spec, prices, judge_alias)
    print(f"{len(spec.tasks())} agent calls ({len(models)} models × {len(spec.suite.cases)} cases × {args.repeats} repeats)"
          f"{'' if args.no_judge else ' + judge'}; estimated cost ≈ {format_usd(estimate)}")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("aborted")
        return 1

    session = boto3.Session(profile_name=args.profile, region_name=cfg.region)
    deployment = load_deployment(cfg, session)
    judge = None if args.no_judge else Judge(cfg.judge, deployment.judge_profile, session.client("bedrock-runtime", region_name=cfg.region))
    total = len(spec.tasks())
    done = 0

    def progress(row) -> None:
        nonlocal done
        done += 1
        status = "error " + row.error["type"] if row.error else f"score {row.score}"
        print(f"[{done}/{total}] {row.model} {row.case_id}#{row.repeat}: {status}", flush=True)

    outcome = execute_run(
        spec, cfg=cfg, prices=prices, deployment=deployment, client=AgentClient.from_session(deployment, session), judge=judge,
        store=RunStore(s3=session.client("s3"), bucket=deployment.results_bucket), estimate=estimate,
        cloudwatch=session.client("cloudwatch"), on_row=progress,
    )
    _print_table(summary_table(outcome.summaries))
    print("\n".join(outcome.locations))
    return 0


def _report_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="bench/run.py report")
    ap.add_argument("run_id")
    ap.add_argument("--reprice", action="store_true", help="use the current pricing.yaml instead of the run's snapshot")
    ap.add_argument("--profile")
    args = ap.parse_args(argv)

    cfg = load_config()
    session = boto3.Session(profile_name=args.profile, region_name=cfg.region)
    deployment = load_deployment(cfg, session)
    store = RunStore(s3=session.client("s3"), bucket=deployment.results_bucket)
    _, summaries = rerender(args.run_id, store=store, cfg=cfg, prices=load_pricing() if args.reprice else None,
                            cloudwatch=session.client("cloudwatch"))
    _print_table(summary_table(summaries))
    print(f"report updated: {store.local_root / args.run_id / 'report.html'} and s3://{deployment.results_bucket}/runs/{args.run_id}/report.html")
    return 0


def run_main(argv: list[str]) -> int:
    try:
        return _run_main(argv)
    except (ConfigError, DeploymentNotFound) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def report_main(argv: list[str]) -> int:
    try:
        return _report_main(argv)
    except (FileNotFoundError, DeploymentNotFound, ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            print("run not found", file=sys.stderr)
            return 2
        raise


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "report":
        return report_main(argv[1:])
    return run_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
