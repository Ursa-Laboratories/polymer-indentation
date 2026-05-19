#!/usr/bin/env python3
"""
Bridge the workcell SQLite result store to the ASMI_new analysis pipeline.

Reads ASMI rows for ``EXPERIMENT_ID`` from ``results/polymer_indent.db``, decodes
the per-well indentation measurements (the same dict that
``cubos.instruments.asmi.ASMI.indentation`` returns), and writes each well as
a CSV in the 5-column layout that ``src.analysis.IndentationAnalyzer`` expects:

    Timestamp(s),Z_Position(mm),Raw_Force(N),Corrected_Force(N),Direction

If ASMI_new is importable (``ASMI_NEW_PATH`` below points at a checkout), the
script also runs the Hertzian/linear fit per well and writes ``summary.csv``.
Without ASMI_new the CSVs are still emitted and can be analyzed by hand or by
running ``main_asmi_with_curetime.py`` in analyze-only mode.

Edit the SETTINGS block and run:
    python scripts/analyze_experiment.py
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

# =============================================================================
# SETTINGS — edit these
# =============================================================================
EXPERIMENT_ID = "bioadhesives_pilot_full_loop"
RESULTS_DB = Path("results/polymer_indent.db")
OUTPUT_ROOT = Path("results/measurements")

# Pointed at an ASMI_new checkout; we add ``<this>/src`` ancestor to sys.path
# so ``from src.analysis import IndentationAnalyzer`` resolves. Set to None to
# only emit CSVs (skip analysis).
ASMI_NEW_PATH: Path | None = Path("/Users/charl/Programming/panda/ASMI_new")

CONTACT_METHOD = "retrospective"     # "extrapolation" | "retrospective" | "simple_threshold"
FIT_METHOD = "hertzian"              # "hertzian" | "linear"
APPLY_SYSTEM_CORRECTION = True
# =============================================================================


_CONTACT_KEY_MAP = {
    "extrapolation": "true_contact",
    "retrospective": "retrospective",
    "simple_threshold": "simple_threshold",
}


def load_asmi_runs(db_path: Path, experiment_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (well, indentation_dict) for each successful ASMI step in the experiment.

    ``result_json`` for a station step is the cubos ``scan`` return — a single
    ``{well_id: indentation_result}`` entry because the workcell rewrites the
    YAML to one well per run.
    """
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT well, result_json FROM runs "
            "WHERE experiment_id = ? AND kind = 'asmi' AND success = 1 "
            "ORDER BY started_at",
            (experiment_id,),
        ).fetchall()
    finally:
        con.close()
    for well, result_json in rows:
        if not result_json:
            continue
        payload = json.loads(result_json)
        # The scan command returns Dict[well_id, indentation_result]. With the
        # workcell's per-well YAML rewrite there's exactly one entry.
        for _well_id, indentation in payload.items():
            if isinstance(indentation, dict) and "measurements" in indentation:
                yield well, indentation


def write_well_csv(out_path: Path, well: str, indentation: dict[str, Any]) -> None:
    """Write one well's indentation in the IndentationAnalyzer 5-column layout.

    Metadata rows mirror the legacy ``simple_indentation_measurement`` header
    closely enough for ``determine_poisson_ratio`` and ``detect_force_limit_reached``
    to work.
    """
    measurements = indentation["measurements"]
    baseline_avg = indentation.get("baseline_avg", 0.0)
    baseline_std = indentation.get("baseline_std", 0.0)
    force_exceeded = indentation.get("force_exceeded", False)
    t0 = measurements[0]["timestamp"] if measurements else 0.0
    target_z = min((m["z_mm"] for m in measurements), default=0.0)

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Test_Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["Well", well])
        w.writerow(["Target_Z(mm)", f"{target_z:.3f}"])
        w.writerow(["Force_Exceeded", str(force_exceeded)])
        w.writerow(["Baseline_Force(N)", f"{baseline_avg:.4f}"])
        w.writerow(["Baseline_Std(N)", f"{baseline_std:.4f}"])
        w.writerow([])
        w.writerow(["Timestamp(s)", "Z_Position(mm)", "Raw_Force(N)", "Corrected_Force(N)", "Direction"])
        for m in measurements:
            w.writerow([
                f"{m['timestamp'] - t0:.3f}",
                f"{m['z_mm']:.4f}",
                f"{m.get('raw_force_n', 0.0):.4f}",
                f"{m.get('corrected_force_n', 0.0):.4f}",
                m.get("direction", "down"),
            ])


def split_up_down_csv(orig_csv_path: Path) -> tuple[Path | None, Path | None]:
    """Split a 5-column CSV with a Direction column into ``_down.csv`` / ``_up.csv``.

    Lifted from ``main_asmi_with_curetime.split_up_down_csv``. The template's
    module-level imports don't resolve in this repo (``src.ForceMonitoring`` is
    missing), so we vendor the helper rather than import the template.
    """
    with open(orig_csv_path, "r") as f:
        rows = [r for r in csv.reader(f) if r]

    metadata: list[list[str]] = []
    data: list[list[str]] = []
    header: list[str] | None = None
    for r in rows:
        if len(r) >= 4 and r[0].replace(".", "", 1).replace("-", "", 1).isdigit():
            data.append(r)
        elif r and r[0] == "Timestamp(s)":
            header = r
        else:
            metadata.append(r)

    if not data:
        return None, None

    down = [r for r in data if not (len(r) >= 5 and r[4] == "up")]
    up = [r for r in data if len(r) >= 5 and r[4] == "up"]
    # Align the return sweep so analysis sees monotonic depth.
    up.sort(key=lambda r: abs(float(r[1])))

    def write_subset(path: Path, subset: list[list[str]], label: str) -> None:
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            for m in metadata:
                w.writerow(m)
            w.writerow(["Direction_File", label])
            w.writerow([])
            w.writerow(header or ["Timestamp(s)", "Z_Position(mm)", "Raw_Force(N)", "Corrected_Force(N)", "Direction"])
            for r in subset:
                w.writerow(r)

    root = orig_csv_path.with_suffix("")
    down_path = root.with_name(root.name + "_down.csv") if down else None
    up_path = root.with_name(root.name + "_up.csv") if up else None
    if down_path:
        write_subset(down_path, down, "down")
    if up_path:
        write_subset(up_path, up, "up")
    return down_path, up_path


def maybe_import_analyzer():
    """Return (IndentationAnalyzer, plotter) if ASMI_new's src is importable, else (None, None)."""
    if not ASMI_NEW_PATH:
        return None, None
    if not ASMI_NEW_PATH.exists():
        print(f"⚠️  ASMI_NEW_PATH does not exist: {ASMI_NEW_PATH} — skipping analysis")
        return None, None
    sys.path.insert(0, str(ASMI_NEW_PATH))
    try:
        from src.analysis import IndentationAnalyzer  # type: ignore[import-not-found]
        from src.plot import plotter  # type: ignore[import-not-found]
        return IndentationAnalyzer, plotter
    except ImportError as exc:
        print(f"⚠️  could not import ASMI_new src ({exc}) — skipping analysis")
        return None, None


def analyze_csv(IndentationAnalyzer, csv_path: Path, well_label: str):
    """Reuse IndentationAnalyzer.analyze_well on one CSV. Returns AnalysisResult or None."""
    analyzer = IndentationAnalyzer(str(csv_path.parent))
    if not analyzer.load_data(csv_path.name):
        return None
    return analyzer.analyze_well(
        well=well_label,
        poisson_ratio=None,
        filename=str(csv_path),
        contact_method=_CONTACT_KEY_MAP.get(CONTACT_METHOD, "true_contact"),
        fit_method=FIT_METHOD,
        apply_system_correction=APPLY_SYSTEM_CORRECTION,
    )


def write_summary(run_dir: Path, results: list) -> Path:
    """Emit a per-well summary CSV (Hertzian E or linear k, depending on FIT_METHOD)."""
    summary_path = run_dir / "summary.csv"
    has_linear = any(getattr(r, "spring_constant", None) is not None for r in results if r)
    with open(summary_path, "w", newline="") as f:
        w = csv.writer(f)
        if has_linear:
            w.writerow(["Well", "SpringConstant_k", "Intercept_b", "R2"])
            for r in results:
                if r:
                    w.writerow([r.well, r.spring_constant, r.linear_intercept, r.linear_fit_quality])
        else:
            w.writerow(["Well", "ElasticModulus_Pa", "Uncertainty", "R2", "PoissonRatio", "MaterialType"])
            for r in results:
                if r:
                    w.writerow([r.well, r.elastic_modulus, r.uncertainty, r.fit_quality,
                                r.poisson_ratio, r.material_type])
    return summary_path


def main() -> int:
    if not RESULTS_DB.exists():
        print(f"❌ results DB not found: {RESULTS_DB}")
        return 1

    runs = list(load_asmi_runs(RESULTS_DB, EXPERIMENT_ID))
    if not runs:
        print(f"❌ no successful ASMI rows for experiment {EXPERIMENT_ID!r} in {RESULTS_DB}")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = OUTPUT_ROOT / f"run_{EXPERIMENT_ID}_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    measure_with_return = any(idn.get("measure_with_return") for _, idn in runs)
    print(f"📁 writing {len(runs)} per-well CSV(s) to {run_dir}  (measure_with_return={measure_with_return})")

    csv_specs: list[tuple[Path, str]] = []  # (csv_path, well_label) per file to analyze
    for well, indentation in runs:
        csv_path = run_dir / f"well_{well}_{stamp}.csv"
        write_well_csv(csv_path, well, indentation)
        if measure_with_return:
            down_path, up_path = split_up_down_csv(csv_path)
            if down_path:
                csv_specs.append((down_path, f"{well}_down"))
            if up_path:
                csv_specs.append((up_path, f"{well}_up"))
        else:
            csv_specs.append((csv_path, well))

    IndentationAnalyzer, _plotter = maybe_import_analyzer()
    if IndentationAnalyzer is None:
        print(f"📊 CSVs written. To analyze, run:")
        print(f"   set existing_run_folder='{run_dir}' in main_asmi_with_curetime.py and call main(do_measure=False).")
        return 0

    results = [analyze_csv(IndentationAnalyzer, p, label) for p, label in csv_specs]
    summary = write_summary(run_dir, results)
    print(f"📊 analysis complete · {sum(1 for r in results if r)}/{len(results)} wells fit · summary={summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
