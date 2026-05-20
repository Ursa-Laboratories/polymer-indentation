#!/usr/bin/env python3
"""
Bioadhesives full workcell loop.

For each ``(source_tube, target_well)`` transfer in ``TRANSFERS``:

    Opentrons source tube -> plate well
    arm: Opentrons -> UV curing station
    SHARC UV cure for that same well
    arm: UV curing station -> ASMI
    ASMI indentation for that same well
    arm: ASMI -> Opentrons, except the final well returns to FINAL_RETURN_LOCATION

Edit the SETTINGS block below and run:
    python main_bioadhesives_workcell.py

How this script fits into the workcell stack
--------------------------------------------
This file is pure configuration + glue. The actual per-well orchestration
lives in ``polymer_indent.loop.run_experiment``, which calls (per well, in
order):

  1.  ``OpentronsClient.run_fill``  — generates a fresh one-transfer Flex
                                      protocol from the SETTINGS below and
                                      ships it to the Opentrons HTTP API.
  2a. ``CubOSStationClient.run_protocol`` on SHARC — home-only protocol so
                                      the gantry is parked before the arm
                                      deposits the plate.
  2.  ``ArmRailClient.transfer``    — opentrons -> uv_station.
  3.  ``CubOSStationClient.run_protocol`` on SHARC — UV cure YAML built by
                                      ``apply_overrides`` + ``render_protocol``
                                      (well id and per-transfer
                                      ``exposure_time`` swapped per iteration).
  4a. ``CubOSStationClient.run_protocol`` on ASMI  — home-only protocol so
                                      the ASMI gantry is parked before the arm
                                      deposits the plate.
  4.  ``ArmRailClient.transfer``    — uv_station -> asmi.
  5.  ``CubOSStationClient.run_protocol`` on ASMI  — indentation YAML built
                                      the same way as SHARC.
  6.  ``ArmRailClient.transfer``    — asmi -> opentrons (or, on the last
                                      well, to ``FINAL_RETURN_LOCATION``).

The arm worker has a fixed route table keyed on the location names above
(``opentrons``, ``uv_station``, ``asmi``, ``storage_end``, ``storage_start``);
the ``*_SLOT`` settings here pick the Opentrons deck slots used by the
generated dispense protocol and must agree with the arm worker's
opentrons-side pickup point. SHARC/ASMI tunables ride on the cubos
protocol YAML via ``apply_overrides``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# =============================================================================
# SETTINGS — edit these
# =============================================================================
EXPERIMENT_ID = "bioadhesives_pilot_full_loop"
CONTROLLER_CONFIG = "configs/controller.yaml"

# Per-well transfers: (source_tube_well, target_plate_well, uv_exposure_s).
# Tube rack wells available: A1, B1, A2, B2, A3, B3.
TRANSFERS = [
    ("A1", "A1", 1.0),
    ("A1", "A2", 2.0),
    ("A1", "A3", 3.0),
    ("B1", "B1", 1.0),
    ("B1", "B2", 2.0),
    ("B1", "B3", 3.0),
]

# Opentrons deck slots — must match the arm worker's opentrons pickup point.
OPENTRONS_TIP_RACK_SLOT = "A2"
OPENTRONS_TUBE_RACK_SLOT = "B2"
OPENTRONS_PLATE_SLOT = "D1"

# Opentrons plate labware. Must be a load_name in the Flex labware library.
# SHARC and ASMI specify their plate in their respective deck.yaml; this SETTING
# is the Opentrons-side equivalent.
OPENTRONS_PLATE_LABWARE = "corning_96_wellplate_360ul_flat"

# Opentrons viscous transfer settings
OPENTRONS_VOLUME_UL = 100
OPENTRONS_FLOW_RATE_UL_MIN = 150
OPENTRONS_AIR_EXPULSION_UL = 20
OPENTRONS_TIP_LIFT_HEIGHT_MM = 8

# UV cure (SHARC station). Per-transfer exposure_time lives in TRANSFERS above.
UV_INTENSITY = 1

# ASMI indentation
ASMI_INDENT_LIMIT_HEIGHT = 1.5
ASMI_MEASURE_WITH_RETURN = True   # record up-sweep samples in addition to descent

# Where the plate goes after the final ASMI run.
FINAL_RETURN_LOCATION = "storage_end"

# Set True to skip the Opentrons dispense — the OpentronsClient placeholder
# (no base_url) logs a warning and returns success, so arm + SHARC + ASMI run
# end-to-end while the result store still gets an "opentrons_fill" row.
SKIP_OPENTRONS_FILL = False
# =============================================================================


sys.path.insert(0, str(Path(__file__).resolve().parent))

from polymer_indent.clients import OpentronsClient  # noqa: E402
from polymer_indent.config import load_controller_config  # noqa: E402
from polymer_indent.experiment import Experiment  # noqa: E402
from polymer_indent.loop import run_experiment  # noqa: E402
from polymer_indent.protocol_render import apply_overrides  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("polymer_indent.bioadhesives")


def main() -> int:
    cfg = load_controller_config(CONTROLLER_CONFIG)

    shared = {
        "volume_ul": OPENTRONS_VOLUME_UL,
        "flow_rate_ul_min": OPENTRONS_FLOW_RATE_UL_MIN,
        "air_expulsion_ul": OPENTRONS_AIR_EXPULSION_UL,
        "tip_lift_height_mm": OPENTRONS_TIP_LIFT_HEIGHT_MM,
        "tip_rack_slot": OPENTRONS_TIP_RACK_SLOT,
        "tube_rack_slot": OPENTRONS_TUBE_RACK_SLOT,
        "plate_slot": OPENTRONS_PLATE_SLOT,
        "plate_labware": OPENTRONS_PLATE_LABWARE,
        "uv_intensity": UV_INTENSITY,
        "asmi_indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT,
    }
    experiment = Experiment(
        id=EXPERIMENT_ID,
        wells=[target for _, target, _ in TRANSFERS],
        params={
            target: {**shared, "source_well": source, "formulation": source,
                     "uv_exposure_s": uv_exposure_s}
            for source, target, uv_exposure_s in TRANSFERS
        },
        defaults={},
        final_well_return_location=FINAL_RETURN_LOCATION,
        raw={"opentrons_transfers": TRANSFERS},
    )

    sharc = cfg.station_bundle("sharc")
    # Only intensity is global; exposure_time is overridden per-well from
    # params["uv_exposure_s"] inside _run_one_well.
    sharc.base_protocol_yaml = apply_overrides(
        sharc.base_protocol_yaml,
        method_kwargs={"intensity": UV_INTENSITY},
    )
    asmi = cfg.station_bundle("asmi")
    asmi.base_protocol_yaml = apply_overrides(
        asmi.base_protocol_yaml,
        scalar={"indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT},
        method_kwargs={"measure_with_return": ASMI_MEASURE_WITH_RETURN},
    )

    log.info("=" * 72)
    log.info("bioadhesives full loop: %d transfers", len(TRANSFERS))
    for source, target, uv_s in TRANSFERS:
        log.info("  Opentrons %s -> plate %s, UV %.1fs, then ASMI %s",
                 source, target, uv_s, target)
    log.info("=" * 72)

    opentrons = OpentronsClient(None) if SKIP_OPENTRONS_FILL else cfg.opentrons_client()
    if SKIP_OPENTRONS_FILL:
        log.warning("SKIP_OPENTRONS_FILL=True — Opentrons step will be a no-op placeholder")

    with cfg.result_store() as results:
        failed = run_experiment(
            experiment,
            opentrons=opentrons,
            arm=cfg.arm_client(),
            sharc=sharc,
            asmi=asmi,
            results=results,
            mock_mode=False,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
