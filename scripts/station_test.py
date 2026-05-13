"""Shared logic for the per-station test scripts (scripts/test_asmi.py, scripts/test_uv.py).

Run from the controller box. Reads the frozen gantry + deck YAML and the base
protocol from ``configs/`` (paths come from ``configs/controller.yaml`` unless
overridden), swaps in the requested well (and any optional param overrides),
POSTs to that station's worker, and prints the result. Use ``--mock`` for a dry
run (the Pi touches no hardware) and ``--validate-only`` to just call
``/validate-protocol``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Work from a plain checkout too (no `pip install -e .` needed).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from polymer_indent.clients import CubOSStationClient, StationRunError  # noqa: E402
from polymer_indent.clients._http import HttpError  # noqa: E402
from polymer_indent.protocol_render import apply_overrides, render_protocol  # noqa: E402

# station key in configs/controller.yaml -> (human label, kind of method_kwargs it takes)
_STATIONS = {
    "asmi": {"label": "ASMI indentation", "kwargs": "asmi"},
    "sharc": {"label": "UV-curing (SHARC)", "kwargs": "uv"},
}

_BAR = "=" * 74
_SEP = "-" * 74


# --------------------------------------------------------------------------- args

def _build_parser(station_key: str) -> argparse.ArgumentParser:
    info = _STATIONS[station_key]
    p = argparse.ArgumentParser(
        description=f"Run a {info['label']} protocol end to end against the {station_key} station worker.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--well", default=None,
                   help="well to target, e.g. E5 (default: whatever's in the base protocol)")

    src = p.add_argument_group("where to send / what to send")
    src.add_argument("--config", default=str(REPO_ROOT / "configs" / "controller.yaml"),
                     help="controller config to read the station base_url + file paths from")
    src.add_argument("--url", default=None,
                     help="station base URL, overrides --config (e.g. http://10.210.29.17:8000)")
    src.add_argument("--gantry", default=None, help="gantry YAML path, overrides --config")
    src.add_argument("--deck", default=None, help="deck YAML path, overrides --config")
    src.add_argument("--protocol", default=None,
                     help="base protocol YAML path, overrides --config "
                          "(point this at a scan protocol to run the whole plate — needs the "
                          "station allow-list to include the 'scan' command)")

    flow = p.add_argument_group("flow")
    flow.add_argument("--mock", action="store_true", help="dry run on the Pi (no hardware)")
    flow.add_argument("--validate-only", action="store_true", help="only call /validate-protocol, then stop")
    flow.add_argument("--no-validate", action="store_true", help="skip the pre-run /validate-protocol call")
    flow.add_argument("-y", "--yes", action="store_true", help="don't prompt before a real (non-mock) run")
    flow.add_argument("--timeout", type=float, default=None, help="HTTP read timeout (s); default = station's")

    ov = p.add_argument_group("optional protocol overrides (left as-is in the base protocol if unset)")
    ov.add_argument("--measurement-height", type=float, default=None,
                    help="labware-relative action height (mm above the well surface; negative = below)")
    if station_key == "asmi":
        ov.add_argument("--indentation-limit-height", type=float, default=None,
                        help="labware-relative deepest descent height (mm; must be <= measurement-height)")
        ov.add_argument("--step-size", type=float, default=None, help="indentation step size (mm)")
        ov.add_argument("--force-limit", type=float, default=None, help="force limit (N)")
        ov.add_argument("--baseline-samples", type=int, default=None, help="baseline force samples")
    else:  # sharc / uv
        ov.add_argument("--intensity", type=float, default=None, help="UV intensity (1-100 percent)")
        ov.add_argument("--exposure-time", type=float, default=None, help="UV exposure time (s)")
    # --interwell-scan-height only matters for scan protocols, but harmless to expose:
    ov.add_argument("--interwell-scan-height", type=float, default=None,
                    help="(scan protocols only) between-wells XY-travel height, labware-relative (mm)")
    return p


_SCALAR_FIELDS = ("measurement_height", "indentation_limit_height", "interwell_scan_height")
_METHOD_KWARGS_BY_STATION = {
    "asmi":  ("step_size", "force_limit", "baseline_samples"),
    "sharc": ("intensity", "exposure_time"),
}


def _collect_overrides(args, station_key: str) -> dict:
    def _pull(names):
        return {n: getattr(args, n) for n in names if getattr(args, n, None) is not None}
    return {"scalar": _pull(_SCALAR_FIELDS),
            "method_kwargs": _pull(_METHOD_KWARGS_BY_STATION[station_key])}


# --------------------------------------------------------------------------- protocol build

def _resolve(path: str | Path) -> Path:
    path = Path(path).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path)


# --------------------------------------------------------------------------- run

def run(station_key: str, argv=None) -> int:
    if station_key not in _STATIONS:
        raise ValueError(f"unknown station {station_key!r}; expected one of {sorted(_STATIONS)}")
    info = _STATIONS[station_key]
    args = _build_parser(station_key).parse_args(argv)

    with open(args.config) as f:
        controller_cfg = yaml.safe_load(f) or {}
    st = (controller_cfg.get("stations") or {}).get(station_key, {}) or {}

    base_url = args.url or st.get("base_url")
    if not base_url:
        print(f"ERROR: no base_url for station {station_key!r} — set it in {args.config} or pass --url",
              file=sys.stderr)
        return 2
    gantry_path = _resolve(args.gantry or st["gantry_config"])
    deck_path = _resolve(args.deck or st["deck_config"])
    protocol_path = _resolve(args.protocol or st["base_protocol"])
    timeout = args.timeout if args.timeout is not None else float(st.get("timeout_s", 900.0))

    gantry_yaml = gantry_path.read_text()
    deck_yaml = deck_path.read_text()
    protocol_yaml = protocol_path.read_text()

    if args.well:
        try:
            protocol_yaml = render_protocol(protocol_yaml, args.well)
        except ValueError as exc:
            print(f"note: couldn't swap a well into {protocol_path.name} ({exc}); sending it verbatim")
    overrides = _collect_overrides(args, station_key)
    protocol_yaml = apply_overrides(protocol_yaml, scalar=overrides["scalar"],
                                     method_kwargs=overrides["method_kwargs"])

    print(_BAR)
    print(f"{info['label']} test   station={station_key}   url={base_url}   mock={args.mock}")
    print(f"  gantry  : {gantry_path}")
    print(f"  deck    : {deck_path}")
    print(f"  protocol: {protocol_path}" + (f"   (well -> {args.well})" if args.well else ""))
    print(_SEP)
    print(protocol_yaml.rstrip())
    print(_BAR)

    client = CubOSStationClient(
        base_url, station_key,
        gantry_config_yaml=gantry_yaml, deck_config_yaml=deck_yaml,
        timeout_s=timeout, mock_mode=args.mock,
    )

    # health
    try:
        h = client.health()
        print(f"health: status={h.get('status')} cubos={h.get('cubos_version')} "
              f"busy={h.get('busy')} allow={h.get('allow')}")
    except HttpError as exc:
        print(f"health: UNREACHABLE — {exc}", file=sys.stderr)
        return 3

    # validate
    if args.validate_only or not args.no_validate:
        try:
            v = client.validate_protocol(protocol_yaml)
        except HttpError as exc:
            print(f"validate-protocol: HTTP error — {exc}", file=sys.stderr)
            return 4
        if v.get("valid"):
            print(f"validate-protocol: OK ({v.get('steps')} steps)")
        else:
            print(f"validate-protocol: INVALID — {v.get('error')}", file=sys.stderr)
            return 4
    if args.validate_only:
        return 0

    # confirm a real run
    if not args.mock and not args.yes:
        print()
        print(f"!! REAL run on {info['label']} (well={args.well or 'as in base protocol'}). "
              "Hardware WILL move.")
        print("!! Confirm: deck/plate/instrument in place, gantry homed-safe, e-stop within reach.")
        if input("Type 'yes' to proceed: ").strip().lower() != "yes":
            print("aborted.")
            return 130

    run_id = f"test:{station_key}:{args.well or 'base'}:{int(time.time())}"
    print(f"\nrun-protocol  run_id={run_id} ...")
    try:
        resp = client.run_protocol(
            run_id=run_id, protocol_yaml=protocol_yaml,
            metadata={"source": "station_test", "station": station_key, "well": args.well},
        )
    except StationRunError as exc:
        print(f"\n!! RUN FAILED: {exc}", file=sys.stderr)
        if exc.payload.get("traceback"):
            print(exc.payload["traceback"], file=sys.stderr)
        else:
            print(json.dumps(exc.payload, indent=2, default=str)[:4000], file=sys.stderr)
        return 1
    except HttpError as exc:
        print(f"\n!! HTTP ERROR: {exc}", file=sys.stderr)
        return 1

    artifacts = resp.get("artifacts") or {}
    print("\nRUN OK")
    print(f"  station       : {resp.get('station_id')}")
    print(f"  cubos_version : {resp.get('cubos_version')}")
    print(f"  protocol_sha  : {resp.get('protocol_sha256')}")
    for k, val in artifacts.items():
        print(f"  {k:<14}: {val}")
    print(f"  results       : {_summarize_results(resp.get('results'))}")
    full = json.dumps(resp.get("results"), indent=2, default=str)
    if len(full) <= 4000:
        print("    " + full.replace("\n", "\n    "))
    else:
        print("    " + full[:4000].replace("\n", "\n    "))
        print(f"    ... ({len(full)} chars total — full result at {artifacts.get('result_path', '<run dir>/result.json')})")
    return 0


def _summarize_results(results) -> str:
    """One-line summary of cubos' per-step results list (which can be huge)."""
    if not isinstance(results, list):
        return repr(results)[:200]
    parts = []
    for i, step in enumerate(results):
        if step is None:
            parts.append(f"{i}:none")
        elif isinstance(step, dict):
            if "data_points" in step:
                parts.append(f"{i}:indent(n={step.get('data_points')},force_exceeded={step.get('force_exceeded')})")
            elif "readings" in step or "mean_n" in step:
                parts.append(f"{i}:measure(mean_n={step.get('mean_n')})")
            else:
                parts.append(f"{i}:dict({len(step)} keys)")
        elif isinstance(step, list):
            parts.append(f"{i}:list({len(step)})")
        else:
            parts.append(f"{i}:{type(step).__name__}")
    return f"{len(results)} steps  [" + ", ".join(parts) + "]"


__all__ = ["run"]
