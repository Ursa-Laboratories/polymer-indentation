"""Tests for the per-station test-script helpers (scripts/station_test.py)."""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import station_test  # noqa: E402


def test_help_does_not_crash():
    # ArgumentDefaultsHelpFormatter does `help % params`, so a literal `%` in a
    # help string would blow up — guard against that regression.
    for station in ("asmi", "sharc"):
        parser = station_test._build_parser(station)
        text = parser.format_help()
        assert "--well" in text


def test_apply_overrides_asmi():
    from polymer_indent.protocol_render import apply_overrides, render_protocol
    base = (REPO_ROOT / "configs" / "protocol" / "asmi_indentation_test.yaml").read_text()
    swapped = render_protocol(base, "C7")
    out = apply_overrides(
        swapped,
        scalar={"indentation_limit_height": -3.0},
        method_kwargs={"force_limit": 5.0, "step_size": 0.02},
    )
    doc = yaml.safe_load(out)
    measure = next(s["measure"] for s in doc["protocol"] if "measure" in s)
    assert measure["position"] == "plate.C7"
    assert measure["indentation_limit_height"] == -3.0
    assert measure["method_kwargs"]["force_limit"] == 5.0
    assert measure["method_kwargs"]["step_size"] == 0.02
    # untouched kwargs survive
    assert measure["method_kwargs"]["baseline_samples"] == 10


def test_apply_overrides_noop_when_empty():
    from polymer_indent.protocol_render import apply_overrides
    base = "protocol:\n  - home:\n"
    assert apply_overrides(base) == base


def test_summarize_results():
    summary = station_test._summarize_results(
        [None, {"data_points": 700, "force_exceeded": False}, None]
    )
    assert "3 steps" in summary and "indent(n=700" in summary
    assert station_test._summarize_results([None, {"mean_n": 1.2, "readings": [1, 2]}]).count("measure") == 1
    assert station_test._summarize_results("oops").startswith("'oops'")


def test_run_errors_without_base_url(tmp_path, capsys):
    cfg = tmp_path / "controller.yaml"
    cfg.write_text("stations:\n  asmi: {}\n")
    rc = station_test.run("asmi", ["--config", str(cfg), "--url", ""])
    assert rc == 2
    assert "no base_url" in capsys.readouterr().err
