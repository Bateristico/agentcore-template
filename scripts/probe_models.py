"""Check that Bedrock models work with this template (Converse + application inference profile).

    uv run scripts/probe_models.py --profile <profile>
    uv run scripts/probe_models.py --profile <profile> --models global.xai.grok-4.7,global.zai.glm-5.3
"""

from __future__ import annotations

import argparse
from pathlib import Path

import boto3

from template_core.config import DEFAULT_CONFIG, load_config
from template_core.probe import format_probe_table, probe_model


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="AWS CLI profile")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--models", help="comma-separated system profile ids (default: all models in deploy.yaml)")
    ap.add_argument("--out", help="also write the markdown table to this file")
    args = ap.parse_args()

    cfg = load_config(args.config)
    session = boto3.Session(profile_name=args.profile, region_name=cfg.region)
    account = session.client("sts").get_caller_identity()["Account"]
    ids = args.models.split(",") if args.models else [m.profile for m in cfg.models.values()]
    bedrock = session.client("bedrock", region_name=cfg.region)
    runtime = session.client("bedrock-runtime", region_name=cfg.region)

    results = []
    for profile_id in ids:
        print(f"probing {profile_id} ...", flush=True)
        results.append(
            probe_model(bedrock, runtime, profile_id=profile_id.strip(), region=cfg.region, account=account, name_prefix=f"{cfg.prefix}-probe")
        )
    table = format_probe_table(results)
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
