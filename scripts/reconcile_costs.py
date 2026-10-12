"""Compare live token-based estimates with billed cost from Cost Explorer (by tag).

    uv run scripts/reconcile_costs.py --days 7 --profile <profile>
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import boto3

from template_core.console import utf8_stdout
from template_core.config import load_config
from template_core.costs import billed_costs, completed_days_window, live_costs, reconcile
from template_core.pricing import format_usd, load_pricing


def main() -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7, help="whole completed UTC days, ending yesterday (inclusive)")
    ap.add_argument("--profile")
    ap.add_argument("--tolerance", type=float, default=0.10)
    args = ap.parse_args()

    cfg, prices = load_config(), load_pricing()
    session = boto3.Session(profile_name=args.profile, region_name=cfg.region)
    # Whole completed UTC days only: end is midnight UTC today (exclusive), so today's partial day is excluded.
    start_day, end_day, start, end = completed_days_window(datetime.now(UTC).date(), args.days)

    live = live_costs(session.client("cloudwatch"), cfg, prices, start, end)
    billed = billed_costs(session.client("ce", region_name="us-east-1"), cfg, start_day, end_day)
    print(f"window {start_day} .. {end_day} (exclusive, UTC) - billed status: {billed.status}")
    print(billed.message)
    print(f"{'agent':14} {'model':18} {'estimate':>12} {'billed':>12} {'delta':>8}  note")
    for r in reconcile(live, billed, cfg, args.tolerance):
        delta = "-" if r.delta_pct is None else f"{r.delta_pct:+.1f}%"
        flag = "" if r.within is None else ("ok" if r.within else "OUT OF TOLERANCE")
        billed_text = "-" if r.billed is None else format_usd(r.billed)
        print(f"{r.agent:14} {r.model:18} {format_usd(r.estimate):>12} {billed_text:>12} {delta:>8}  {flag} {r.note}")
    if billed.status == "ok":
        for agent, amount in billed.agent_compute.items():
            print(f"AgentCore compute billed to {agent}: {format_usd(amount)}")
        print(f"judge (evaluation) billed: {format_usd(billed.judge)}; untagged within project: {format_usd(billed.untagged)}")
    else:
        print(f"judge and untagged billed: n/a ({billed.status})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
