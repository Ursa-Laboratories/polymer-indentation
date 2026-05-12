#!/usr/bin/env python3
"""Run one arm transfer via the xArm + Vention-rail worker.

Run from the controller box:

    python scripts/test_arm.py --from opentrons --to uv_station --mock     # logging-only
    python scripts/test_arm.py --from opentrons --to uv_station            # real (prompts first)
    python scripts/test_arm.py --from asmi --to storage_end
    python scripts/test_arm.py --health

Valid locations: opentrons, uv_station, asmi, storage_end. Supported routes are
whatever the worker's route table allows (opentrons->uv_station, uv_station->asmi,
asmi->uv_station, asmi->opentrons, asmi->storage_end, opentrons->storage_end).
By default reads the arm worker's base_url from configs/controller.yaml; override
with --url.
"""

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from polymer_indent.clients import ArmRailClient  # noqa: E402
from polymer_indent.clients._http import HttpError  # noqa: E402
from polymer_indent.clients.arm_rail import ArmTransferError  # noqa: E402

_LOCATIONS = ["opentrons", "uv_station", "asmi", "storage_end", "storage_start"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run an xArm + Vention-rail plate transfer via the arm worker.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--from", dest="src", choices=_LOCATIONS, help="pick-up location")
    p.add_argument("--to", dest="dst", choices=_LOCATIONS, help="drop-off location")
    p.add_argument("--config", default=str(REPO_ROOT / "configs" / "controller.yaml"))
    p.add_argument("--url", default=None, help="arm worker base URL, overrides --config (e.g. http://10.210.29.16:5004)")
    p.add_argument("--mock", action="store_true", help="ask the worker to use logging-only stand-ins")
    p.add_argument("--health", action="store_true", help="just GET /health and exit")
    p.add_argument("-y", "--yes", action="store_true", help="don't prompt before a real (non-mock) transfer")
    p.add_argument("--timeout", type=float, default=None)
    args = p.parse_args(argv)

    with open(args.config) as f:
        cfg = yaml.safe_load(f) or {}
    arm_cfg = cfg.get("arm", {}) or {}
    base_url = args.url or arm_cfg.get("base_url")
    if not base_url:
        print(f"ERROR: no arm base_url — set arm.base_url in {args.config} or pass --url", file=sys.stderr)
        return 2
    timeout = args.timeout if args.timeout is not None else float(arm_cfg.get("timeout_s", 300.0))
    client = ArmRailClient(base_url, timeout_s=timeout)

    try:
        h = client.health()
        print(f"arm worker: {h}")
    except HttpError as exc:
        print(f"arm worker UNREACHABLE — {exc}", file=sys.stderr)
        return 3
    if args.health:
        return 0

    if not args.src or not args.dst:
        print("ERROR: --from and --to are required (unless --health)", file=sys.stderr)
        return 2

    if not args.mock and not args.yes:
        print(f"\n!! REAL arm transfer {args.src} -> {args.dst}. The arm/rail WILL move.")
        if input("Type 'yes' to proceed: ").strip().lower() != "yes":
            print("aborted.")
            return 130

    run_id = f"test:arm:{args.src}->{args.dst}:{int(time.time())}"
    print(f"\ntransfer  {args.src} -> {args.dst}  (run_id={run_id}, mock={args.mock}) ...")
    try:
        resp = client.transfer(from_location=args.src, to_location=args.dst,
                                run_id=run_id, mock_mode=True if args.mock else None)
    except ArmTransferError as exc:
        print(f"\n!! TRANSFER FAILED: {exc}", file=sys.stderr)
        return 1
    except HttpError as exc:
        print(f"\n!! HTTP ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"\nTRANSFER OK: {resp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
