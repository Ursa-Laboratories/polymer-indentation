"""Experiment definition: which wells to run and the per-well parameters.

The experiment YAML is intentionally small. Per-well parameters are
``defaults`` merged with the well's own dict. The protocol sent to each station
is the frozen base protocol with this well's id swapped in; per-well numeric
overrides (intensity, exposure, force limit, ...) are *not* applied to the
protocol in this first version — only the well id changes — but they are
recorded with the results so the bookkeeping is complete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

_WELL_RE = re.compile(r"^[A-Za-z]+[0-9]+$")

# Where the final well's plate goes after measurement (non-final wells return
# to the Opentrons deck).
_DEFAULT_FINAL_RETURN = "storage_end"
_NONFINAL_RETURN = "opentrons"


@dataclass
class Experiment:
    """A parsed experiment: id, ordered wells, and resolved per-well params."""

    id: str
    wells: List[str]
    params: Dict[str, Dict[str, Any]]      # well -> resolved params
    defaults: Dict[str, Any] = field(default_factory=dict)
    final_well_return_location: str = _DEFAULT_FINAL_RETURN
    raw: Dict[str, Any] = field(default_factory=dict)

    def well_params(self, well: str) -> Dict[str, Any]:
        return self.params[well]

    def return_location(self, well: str) -> str:
        """Where the arm should take the plate after this well's ASMI step."""
        is_last = well == self.wells[-1]
        return self.final_well_return_location if is_last else _NONFINAL_RETURN

    def items(self):
        """Iterate (well, resolved_params) in declared order."""
        for w in self.wells:
            yield w, self.params[w]


def load_experiment(path: str | Path) -> Experiment:
    """Load and validate an experiment YAML.

    Expected shape::

        experiment:
          id: pegda_screen_001
          defaults: { volume_ul: 350, ... }     # optional
          wells:
            A1: { formulation: pegda_5,  uv_intensity: 20, uv_time: 300 }
            A2: { formulation: pegda_10, uv_intensity: 20, uv_time: 300 }
        final_well_return_location: storage_end   # optional, default "storage_end"
    """
    path = Path(path)
    with path.open() as f:
        doc = yaml.safe_load(f) or {}

    if "experiment" not in doc or not isinstance(doc["experiment"], dict):
        raise ValueError(f"{path}: missing top-level 'experiment:' mapping")
    exp = doc["experiment"]

    exp_id = exp.get("id")
    if not exp_id or not isinstance(exp_id, str):
        raise ValueError(f"{path}: experiment.id must be a non-empty string")

    wells_doc = exp.get("wells")
    if not isinstance(wells_doc, dict) or not wells_doc:
        raise ValueError(f"{path}: experiment.wells must be a non-empty mapping")

    defaults = exp.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError(f"{path}: experiment.defaults must be a mapping")

    wells: List[str] = []
    params: Dict[str, Dict[str, Any]] = {}
    for well, well_params in wells_doc.items():
        well = str(well).strip().upper()
        if not _WELL_RE.match(well):
            raise ValueError(f"{path}: {well!r} is not a well id (expected like 'A1')")
        if well in params:
            raise ValueError(f"{path}: well {well} listed twice")
        if well_params is None:
            well_params = {}
        if not isinstance(well_params, dict):
            raise ValueError(f"{path}: well {well}: parameters must be a mapping")
        merged = {**defaults, **well_params}
        wells.append(well)
        params[well] = merged

    final_return = doc.get("final_well_return_location", _DEFAULT_FINAL_RETURN)
    if not isinstance(final_return, str):
        raise ValueError(f"{path}: final_well_return_location must be a string")

    return Experiment(
        id=exp_id,
        wells=wells,
        params=params,
        defaults=dict(defaults),
        final_well_return_location=final_return,
        raw=doc,
    )
