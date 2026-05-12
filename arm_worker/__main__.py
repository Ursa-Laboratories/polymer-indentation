"""Run the xArm + Vention-rail transfer worker.

    python -m arm_worker                 # real hardware, port 5004
    python -m arm_worker --mock          # logging-only stand-ins (no arm/rail)
    python -m arm_worker --port 5004

Runs on the controller box (bear-den-keeper). Real mode needs `xarm-python-sdk`,
`machine-logic-sdk` (Python 3.10), and the keeper_pc repo's `device_drivers/` on
PYTHONPATH. `--mock` needs only flask.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arm_worker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ARM_WORKER_PORT", 5004)))
    parser.add_argument("--mock", action="store_true", help="default to mock_mode (no hardware)")
    parser.add_argument("--ot-plate", default="black", choices=["black", "transparent"],
                        help="Opentrons D1 plate variant for pickup poses")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    app = create_app(mock_mode_default=args.mock, ot_plate_type=args.ot_plate)
    logging.getLogger("arm_worker").info(
        "arm worker listening on %s:%d (mock_default=%s, ot_plate=%s)",
        args.host, args.port, args.mock, args.ot_plate,
    )
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
