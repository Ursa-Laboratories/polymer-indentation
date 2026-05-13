#!/usr/bin/env python3
"""
polymer-indentation — one-well end-to-end cycle.

    opentrons.fill (placeholder)  →  arm: OT → uv_station
                                  →  sharc.run_protocol  (UV cure)
                                  →  arm: uv_station → asmi
                                  →  asmi.run_protocol   (indentation)
                                  →  arm: asmi → opentrons

Edit the SETTINGS block below and run:
    python main.py

For the YAML/CLI version (multi-well, --resume, --mock, --only-well, etc.)
the `polymer-indent` console script is still wired up:
    polymer-indent run examples/single_well_cycle.yaml
    polymer-indent health
    polymer-indent workers up arm
A bare `python main.py <subcommand> ...` also forwards to that CLI for
backward compat.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# =============================================================================
# SETTINGS — edit these
# =============================================================================
WELL                       = "E5"          # which well on the SBS 96-well plate (e.g. "A1", "B7", "E5")

# UV cure (SHARC station)
UV_INTENSITY               = 1             # OmniCure intensity, 1–100 %
UV_EXPOSURE_S              = 5.0           # OmniCure exposure time, seconds

# ASMI indentation
ASMI_INDENT_LIMIT_HEIGHT   = 1.5           # mm above well surface; lower (or negative) = deeper indent
                                           # 1.5 (≤ measurement_height 2.0) ≈ ~0.5 mm of non-touch motion
                                           # use e.g. -5.0 for a real indent (5 mm into the well)

# Where the plate goes after ASMI ("opentrons" or "storage_end")
RETURN_LOCATION            = "opentrons"

# Bookkeeping
EXPERIMENT_ID              = "single_well_cycle"
CONTROLLER_CONFIG          = "configs/controller.yaml"
# =============================================================================


# Make the package importable when running from the repo without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from polymer_indent.config import load_controller_config  # noqa: E402
from polymer_indent.experiment import Experiment  # noqa: E402
from polymer_indent.loop import run_experiment  # noqa: E402
from polymer_indent.protocol_render import apply_overrides  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("polymer_indent.main")


# Opentrons fill is currently a placeholder — see polymer_indent/clients/opentrons.py
# for the (still-no-op) implementation and the TODO with the real Flex REST flow.
# `cfg.opentrons_client()` below returns that client.


def main() -> int:
    cfg = load_controller_config(CONTROLLER_CONFIG)

    # Build the experiment in code — no experiment.yaml needed for the one-well cycle.
    experiment = Experiment(
        id=EXPERIMENT_ID,
        wells=[WELL],
        params={WELL: {
            "volume_ul": 350,
            "uv_intensity": UV_INTENSITY,
            "uv_time": UV_EXPOSURE_S,
            "asmi_indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT,
        }},
        defaults={},
        final_well_return_location=RETURN_LOCATION,
    )

    # Apply the SETTINGS knobs as overrides on top of the frozen base protocols.
    # (well id is rewritten by the loop.)
    sharc = cfg.station_bundle("sharc")
    sharc.base_protocol_yaml = apply_overrides(
        sharc.base_protocol_yaml,
        method_kwargs={"intensity": UV_INTENSITY, "exposure_time": UV_EXPOSURE_S},
    )
    asmi = cfg.station_bundle("asmi")
    asmi.base_protocol_yaml = apply_overrides(
        asmi.base_protocol_yaml,
        scalar={"indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT},
    )

    arm = cfg.arm_client()
    opentrons = cfg.opentrons_client()

    log.info("=" * 72)
    log.info("polymer-indentation cycle  ·  well=%s  ·  uv: %s%% for %ss  ·  asmi_limit_h=%s  ·  return=%s",
             WELL, UV_INTENSITY, UV_EXPOSURE_S, ASMI_INDENT_LIMIT_HEIGHT, RETURN_LOCATION)
    log.info("=" * 72)

    with cfg.result_store() as results:
        failed = run_experiment(
            experiment,
            opentrons=opentrons, arm=arm, sharc=sharc, asmi=asmi,
            results=results,
            mock_mode=False,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    # If extra args are passed, forward to the polymer-indent CLI
    # (so `python main.py run ...`, `health`, `workers`, etc. still work).
    if len(sys.argv) > 1:
        from polymer_indent.cli import main as cli_main
        sys.exit(cli_main())
    sys.exit(main())
