#!/usr/bin/env python3
"""
Bioadhesives full workcell loop.

For each transfer declared in ``opentrons_bioadhesives_pilot.py``:

    Opentrons source tube -> plate well
    arm: Opentrons -> UV curing station
    SHARC UV cure for that same well
    arm: UV curing station -> ASMI
    ASMI indentation for that same well
    arm: ASMI -> Opentrons, except the final well returns to FINAL_RETURN_LOCATION

Edit the SETTINGS block below and run:
    python main_bioadhesives_workcell.py
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path
from typing import Iterable

# =============================================================================
# SETTINGS - edit these
# =============================================================================
EXPERIMENT_ID = "bioadhesives_pilot_full_loop"
CONTROLLER_CONFIG = "configs/controller.yaml"
OPENTRONS_PILOT_PROTOCOL = "opentrons_bioadhesives_pilot.py"

# Opentrons viscous transfer settings
OPENTRONS_VOLUME_UL = 100
OPENTRONS_FLOW_RATE_UL_MIN = 150
OPENTRONS_AIR_EXPULSION_UL = 20
OPENTRONS_TIP_LIFT_HEIGHT_MM = 8

# UV cure (SHARC station)
UV_INTENSITY = 1
UV_EXPOSURE_S = 5.0

# ASMI indentation
ASMI_INDENT_LIMIT_HEIGHT = 1.5
ASMI_CURE_TIME_S = 0.0

# Where the plate goes after the final ASMI run.
FINAL_RETURN_LOCATION = "storage_end"
# =============================================================================


sys.path.insert(0, str(Path(__file__).resolve().parent))

from polymer_indent.config import load_controller_config  # noqa: E402
from polymer_indent.experiment import Experiment  # noqa: E402
from polymer_indent.loop import run_experiment  # noqa: E402
from polymer_indent.protocol_render import apply_overrides  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("polymer_indent.bioadhesives")


def load_transfers(path: str | Path) -> list[tuple[str, str]]:
    """Read ``TRANSFERS`` from an Opentrons protocol without importing it."""
    protocol_path = Path(path)
    tree = ast.parse(protocol_path.read_text(), filename=str(protocol_path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "TRANSFERS" in names:
                value = ast.literal_eval(node.value)
                return _normalize_transfers(value)
    raise ValueError(f"{protocol_path}: missing TRANSFERS assignment")


def _normalize_transfers(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, Iterable):
        raise ValueError("TRANSFERS must be an iterable of (source_well, target_well) pairs")
    transfers: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError(f"invalid transfer entry: {item!r}")
        source, target = str(item[0]).strip().upper(), str(item[1]).strip().upper()
        if not source or not target:
            raise ValueError(f"invalid transfer entry: {item!r}")
        transfers.append((source, target))
    if not transfers:
        raise ValueError("TRANSFERS must not be empty")
    targets = [target for _, target in transfers]
    if len(set(targets)) != len(targets):
        raise ValueError(f"target wells must be unique for result bookkeeping: {targets!r}")
    return transfers


def main() -> int:
    transfers = load_transfers(OPENTRONS_PILOT_PROTOCOL)
    cfg = load_controller_config(CONTROLLER_CONFIG)

    params = {
        target: {
            "source_well": source,
            "volume_ul": OPENTRONS_VOLUME_UL,
            "flow_rate_ul_min": OPENTRONS_FLOW_RATE_UL_MIN,
            "air_expulsion_ul": OPENTRONS_AIR_EXPULSION_UL,
            "tip_lift_height_mm": OPENTRONS_TIP_LIFT_HEIGHT_MM,
            "formulation": source,
            "uv_intensity": UV_INTENSITY,
            "uv_time": UV_EXPOSURE_S,
            "asmi_indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT,
            "asmi_cure_time_s": ASMI_CURE_TIME_S,
        }
        for source, target in transfers
    }
    experiment = Experiment(
        id=EXPERIMENT_ID,
        wells=[target for _, target in transfers],
        params=params,
        defaults={},
        final_well_return_location=FINAL_RETURN_LOCATION,
        raw={
            "opentrons_transfers": transfers,
            "source_protocol": OPENTRONS_PILOT_PROTOCOL,
        },
    )

    sharc = cfg.station_bundle("sharc")
    sharc.base_protocol_yaml = apply_overrides(
        sharc.base_protocol_yaml,
        method_kwargs={"intensity": UV_INTENSITY, "exposure_time": UV_EXPOSURE_S},
    )

    asmi_method_kwargs = {}
    if ASMI_CURE_TIME_S:
        asmi_method_kwargs["cure_time"] = ASMI_CURE_TIME_S
    asmi = cfg.station_bundle("asmi")
    asmi.base_protocol_yaml = apply_overrides(
        asmi.base_protocol_yaml,
        scalar={"indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT},
        method_kwargs=asmi_method_kwargs or None,
    )

    log.info("=" * 72)
    log.info("bioadhesives full loop: %d transfers", len(transfers))
    for source, target in transfers:
        log.info("  Opentrons %s -> plate %s, then UV + ASMI %s", source, target, target)
    log.info("=" * 72)

    with cfg.result_store() as results:
        failed = run_experiment(
            experiment,
            opentrons=cfg.opentrons_client(),
            arm=cfg.arm_client(),
            sharc=sharc,
            asmi=asmi,
            results=results,
            mock_mode=False,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
