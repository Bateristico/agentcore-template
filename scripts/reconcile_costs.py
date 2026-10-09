"""Compare live token-based estimates with billed cost from Cost Explorer (by tag).

    uv run scripts/reconcile_costs.py --days 7 --profile <profile>
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

import boto3

from template_core.config import load_config
from template_core.costs import billed_costs, live_costs, reconcile
from template_core.pricing import format_usd, load_pricing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="whole UTC days, ending today (inclusive)")
    ap.add_argument("--profile")
    ap.add_argument("--tolerance", type=float, default=0.10)
    args = ap.parse_args()

    cfg, prices = load_config(), load_pricing()
    session = boto3.Session(profile_name=args.profile, region_name=cfg.region)
    end_day = date.today() + timedelta(days=1)
    start_day = end_day - timedelta(days=args.days)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    end = min(datetime.now(UTC), datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC))

    live = live_costs(session.client("cloudwatch"), cfg, prices, start, end)
    billed = billed_costs(session.client("ce", region_name="us-east-1"), cfg, start_day, end_day)
    print(f"window {start_day} .. {end_day} (exclusive) — billed status: {billed.status}")
    print(billed.message)
    print(f"{'agent':14} {'model':18} {'estimate':>12} {'billed':>12} {'delta':>8}  note")
    for r in reconcile(live, billed, cfg, args.tolerance):
        delta = "-" if r.delta_pct is None else f"{r.delta_pct:+.1f}%"
        flag = "" if r.within is None else ("ok" if r.within else "OUT OF TOLERANCE")
        print(f"{r.agent:14} {r.model:18} {format_usd(r.estimate):>12} {format_usd(r.billed) if r.billed else '-':>12} {delta:>8}  {flag} {r.note}")
    for agent, amount in billed.agent_compute.items():
        print(f"AgentCore compute billed to {agent}: {format_usd(amount)}")
    print(f"judge (evaluation) billed: {format_usd(billed.judge)}; untagged within project: {format_usd(billed.untagged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
