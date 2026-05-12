"""The per-well experiment loop.

For each well:
    1. Opentrons fill                        (placeholder client)
    2. arm transfer  opentrons -> uv_station
    3. SHARC UV cure   (send gantry+deck+well-swapped protocol to the SHARC Pi)
    4. arm transfer  uv_station -> asmi
    5. ASMI indentation (send gantry+deck+well-swapped protocol to the ASMI Pi)
    6. results.store(...)
    7. arm transfer  asmi -> {storage_end if last well else opentrons}

This mirrors the design-doc loop. The only thing that changes in the protocol
YAML between iterations is the well id (see ``protocol_render.render_protocol``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .clients import ArmRailClient, CubOSStationClient, OpentronsClient
from .experiment import Experiment
from .protocol_render import render_protocol
from .results import ResultStore

log = logging.getLogger("polymer_indent.loop")


@dataclass
class StationBundle:
    """A station client + its frozen base-protocol text."""

    client: CubOSStationClient
    base_protocol_yaml: str


def run_experiment(
    experiment: Experiment,
    *,
    opentrons: OpentronsClient,
    arm: ArmRailClient,
    sharc: StationBundle,
    asmi: StationBundle,
    results: ResultStore,
    mock_mode: bool = False,
    resume: bool = False,
    only_wells: Optional[Sequence[str]] = None,
    continue_on_error: bool = False,
) -> int:
    """Run the experiment. Returns the number of wells that failed.

    Args:
        resume: skip wells already marked ``done`` in the result store.
        only_wells: if given, only run these wells (still in declared order).
        continue_on_error: keep going after a well fails (default: stop).
    """
    results.start_experiment(experiment)

    wells = list(_select_wells(experiment, only_wells))
    already_done = results.done_wells(experiment.id) if resume else set()

    failed = 0
    for well in wells:
        if well in already_done:
            log.info("well %s: already done — skipping (resume)", well)
            continue
        params = experiment.well_params(well)
        step_id = f"{experiment.id}:{well}"
        return_location = experiment.return_location(well)
        try:
            _run_one_well(
                experiment_id=experiment.id,
                well=well,
                params=params,
                step_id=step_id,
                return_location=return_location,
                opentrons=opentrons,
                arm=arm,
                sharc=sharc,
                asmi=asmi,
                results=results,
                mock_mode=mock_mode,
            )
            results.set_well_status(experiment.id, well, "done")
            log.info("well %s: done", well)
        except Exception as exc:  # noqa: BLE001 — one bad well shouldn't be silent
            failed += 1
            results.set_well_status(experiment.id, well, "failed", error=repr(exc))
            log.exception("well %s: FAILED — %s", well, exc)
            if not continue_on_error:
                results.finish_experiment(experiment.id, "failed")
                raise

    status = "failed" if failed else "completed"
    results.finish_experiment(experiment.id, status)
    log.info("experiment %s: %s (%d/%d wells failed)",
             experiment.id, status, failed, len(wells))
    return failed


def _run_one_well(
    *,
    experiment_id: str,
    well: str,
    params: dict,
    step_id: str,
    return_location: str,
    opentrons: OpentronsClient,
    arm: ArmRailClient,
    sharc: StationBundle,
    asmi: StationBundle,
    results: ResultStore,
    mock_mode: bool,
) -> None:
    # 1. Opentrons fill (placeholder)
    fill = opentrons.run_fill(
        well=well,
        volume_ul=params.get("volume_ul", 350),
        formulation=params.get("formulation"),
        run_id=f"{step_id}:fill",
    )
    results.record_run(
        run_id=f"{step_id}:fill", experiment_id=experiment_id, well=well,
        kind="opentrons_fill", station="opentrons",
        success=bool(fill.get("success", True)), finished_at=None, result=fill,
    )

    # 2. arm: opentrons -> uv_station
    _transfer(arm, results, experiment_id, well, step_id, "move-to-sharc",
              "opentrons", "uv_station", mock_mode=mock_mode)

    # 3. SHARC UV cure
    sharc_run_id = f"{step_id}:sharc"
    sharc_protocol_yaml = render_protocol(sharc.base_protocol_yaml, well)
    sharc_result = sharc.client.run_protocol(
        run_id=sharc_run_id,
        protocol_yaml=sharc_protocol_yaml,
        metadata={"experiment_id": experiment_id, "well": well, "step": "sharc"},
        mock_mode=mock_mode,
    )

    # 4. arm: uv_station -> asmi
    _transfer(arm, results, experiment_id, well, step_id, "move-to-asmi",
              "uv_station", "asmi", mock_mode=mock_mode)

    # 5. ASMI indentation
    asmi_run_id = f"{step_id}:asmi"
    asmi_protocol_yaml = render_protocol(asmi.base_protocol_yaml, well)
    asmi_result = asmi.client.run_protocol(
        run_id=asmi_run_id,
        protocol_yaml=asmi_protocol_yaml,
        metadata={"experiment_id": experiment_id, "well": well, "step": "asmi"},
        mock_mode=mock_mode,
    )

    # 6. bookkeeping
    results.store(
        experiment_id=experiment_id,
        well=well,
        sharc=sharc_result,
        asmi=asmi_result,
        sharc_run_id=sharc_run_id,
        asmi_run_id=asmi_run_id,
        sharc_protocol_yaml=sharc_protocol_yaml,
        asmi_protocol_yaml=asmi_protocol_yaml,
    )

    # 7. arm: asmi -> {storage_end | opentrons}
    _transfer(arm, results, experiment_id, well, step_id, "return",
              "asmi", return_location, mock_mode=mock_mode)


def _transfer(arm, results, experiment_id, well, step_id, tag, src, dst, *, mock_mode=False) -> None:
    run_id = f"{step_id}:{tag}"
    resp = arm.transfer(from_location=src, to_location=dst, run_id=run_id,
                        mock_mode=True if mock_mode else None)
    results.record_run(
        run_id=run_id, experiment_id=experiment_id, well=well,
        kind="arm_transfer", station="xarm",
        success=bool(resp.get("success", True)), finished_at=None,
        result={"from": src, "to": dst, "response": resp},
    )


def _select_wells(experiment: Experiment, only_wells: Optional[Sequence[str]]) -> Iterable[str]:
    if not only_wells:
        yield from experiment.wells
        return
    wanted = {w.strip().upper() for w in only_wells}
    unknown = wanted - set(experiment.wells)
    if unknown:
        raise ValueError(f"--only-well: unknown wells {sorted(unknown)}")
    for w in experiment.wells:
        if w in wanted:
            yield w


__all__ = ["run_experiment", "StationBundle"]
