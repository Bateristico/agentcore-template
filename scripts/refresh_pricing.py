"""Compare config/pricing.yaml with the AWS Price List API; --write updates it.

    uv run scripts/refresh_pricing.py --profile <profile>
    uv run scripts/refresh_pricing.py --profile <profile> --write
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import boto3

from template_core.console import utf8_stdout
from template_core.config import load_config
from template_core.price_list import apply_prices, compare_prices
from template_core.pricing import DEFAULT_PRICING, dump_pricing, load_pricing


def main() -> int:
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--region", help="pricing region (default: deploy.yaml region)")
    ap.add_argument("--pricing", default=str(DEFAULT_PRICING))
    ap.add_argument("--write", action="store_true", help="update the pricing file with changed prices")
    args = ap.parse_args()

    region = args.region or load_config().region
    table = load_pricing(args.pricing)
    client = boto3.Session(profile_name=args.profile).client("pricing", region_name="us-east-1")
    diffs = compare_prices(table, client, region)
    print(f"{'alias':20} {'component':12} {'current':>12} {'price list':>12}  status")
    for d in diffs:
        fetched = "-" if d.fetched is None else str(d.fetched)
        print(f"{d.alias:20} {d.component:12} {d.current!s:>12} {fetched:>12}  {d.status}")
    unpriced_sources = [a for a, p in table.models.items() if p.source is None]
    if unpriced_sources:
        print(f"no Price List source configured for: {', '.join(unpriced_sources)}")
    changed = [d for d in diffs if d.status == "changed"]
    if args.write and changed:
        Path(args.pricing).write_text(dump_pricing(apply_prices(table, diffs, date.today())), encoding="utf-8")
        print(f"updated {len(changed)} price(s) in {args.pricing}")
    elif changed:
        print(f"{len(changed)} price(s) differ; re-run with --write to update")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
