"""Loop behavior with fake device clients — asserts the per-well call order,
last-well routing, bookkeeping, resume, and failure handling.
"""

import pytest

from polymer_indent.experiment import load_experiment
from polymer_indent.loop import StationBundle, run_experiment
from polymer_indent.results import ResultStore

_SHARC_BASE = "protocol:\n  - home:\n  - measure:\n      instrument: uv_curing\n      position: plate_holder.plate.A1\n  - home:\n"
_ASMI_BASE = "protocol:\n  - home:\n  - measure:\n      instrument: asmi\n      position: plate.A1\n  - home:\n"


class FakeOpentrons:
    def __init__(self):
        self.calls = []

    def run_fill(self, *, well, volume_ul, formulation=None, run_id=None):
        self.calls.append(("fill", well, volume_ul, formulation, run_id))
        return {"success": True, "well": well, "volume_dispensed": volume_ul}


class FakeArm:
    def __init__(self):
        self.transfers = []

    def transfer(self, *, from_location, to_location, run_id=None, mock_mode=None):
        self.transfers.append((from_location, to_location, run_id, mock_mode))
        return {"success": True, "from": from_location, "to": to_location}


class FakeStation:
    def __init__(self, name, *, fail_on_well=None):
        self.name = name
        self.runs = []
        self.fail_on_well = fail_on_well

    def run_protocol(self, *, run_id, protocol_yaml, metadata=None, mock_mode=None):
        self.runs.append((run_id, protocol_yaml, metadata, mock_mode))
        well = (metadata or {}).get("well")
        if well == self.fail_on_well:
            from polymer_indent.clients import StationRunError

            raise StationRunError(self.name, run_id, {"error": "boom"})
        return {"success": True, "run_id": run_id, "station_id": self.name,
                "results": [None, {"ok": True}, None], "artifacts": {"run_dir": f"/runs/{run_id}"}}


def _exp(tmp_path, wells=("A1", "A2", "A3"), final="storage_end"):
    if isinstance(wells, str):
        wells = [wells]
    lines = ["experiment:", "  id: e1", "  wells:"]
    lines += [f"    {w}: {{}}" for w in wells]
    lines.append(f"final_well_return_location: {final}")
    p = tmp_path / "exp.yaml"
    p.write_text("\n".join(lines) + "\n")
    return load_experiment(p)


def _bundles():
    return (
        StationBundle(client=FakeStation("sharc"), base_protocol_yaml=_SHARC_BASE),
        StationBundle(client=FakeStation("asmi"), base_protocol_yaml=_ASMI_BASE),
    )


def test_per_well_sequence_and_last_well_routing(tmp_path):
    exp = _exp(tmp_path)
    ot, arm = FakeOpentrons(), FakeArm()
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        failed = run_experiment(exp, opentrons=ot, arm=arm, sharc=sharc, asmi=asmi,
                                results=results, mock_mode=True)
        assert failed == 0
        # 3 wells * 3 transfers each
        assert len(arm.transfers) == 9
        # first well's three legs:
        assert arm.transfers[0][:2] == ("opentrons", "uv_station")
        assert arm.transfers[1][:2] == ("uv_station", "asmi")
        assert arm.transfers[2][:2] == ("asmi", "opentrons")          # non-last well returns to opentrons
        # last well's return leg goes to storage_end
        assert arm.transfers[-1][:2] == ("asmi", "storage_end")
        # each station ran once per well
        assert [r[0] for r in sharc.client.runs] == ["e1:A1:sharc", "e1:A2:sharc", "e1:A3:sharc"]
        assert [r[0] for r in asmi.client.runs] == ["e1:A1:asmi", "e1:A2:asmi", "e1:A3:asmi"]
        # protocol sent to SHARC for well A2 has the well swapped in
        a2_proto = next(p for rid, p, *_ in sharc.client.runs if rid == "e1:A2:sharc")
        assert "plate_holder.plate.A2" in a2_proto and "plate_holder.plate.A1" not in a2_proto
        # bookkeeping
        assert results.well_status("e1", "A3") == "done"
        kinds = {row["kind"] for row in results.runs_for_well("e1", "A1")}
        assert kinds == {"opentrons_fill", "arm_transfer", "sharc", "asmi"}


def test_mock_mode_propagates_to_stations(tmp_path):
    exp = _exp(tmp_path, wells=["A1"])
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                       results=results, mock_mode=True)
    assert sharc.client.runs[0][3] is True
    assert asmi.client.runs[0][3] is True


def test_only_wells(tmp_path):
    exp = _exp(tmp_path)
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                       results=results, mock_mode=True, only_wells=["A2"])
    assert [r[0] for r in sharc.client.runs] == ["e1:A2:sharc"]


def test_failure_aborts_by_default(tmp_path):
    exp = _exp(tmp_path)
    sharc = StationBundle(client=FakeStation("sharc", fail_on_well="A2"), base_protocol_yaml=_SHARC_BASE)
    asmi = StationBundle(client=FakeStation("asmi"), base_protocol_yaml=_ASMI_BASE)
    with ResultStore(tmp_path / "r.db") as results:
        with pytest.raises(Exception):
            run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                           results=results, mock_mode=True)
        assert results.well_status("e1", "A1") == "done"
        assert results.well_status("e1", "A2") == "failed"
        assert results.well_status("e1", "A3") == "pending"


def test_continue_on_error(tmp_path):
    exp = _exp(tmp_path)
    sharc = StationBundle(client=FakeStation("sharc", fail_on_well="A2"), base_protocol_yaml=_SHARC_BASE)
    asmi = StationBundle(client=FakeStation("asmi"), base_protocol_yaml=_ASMI_BASE)
    with ResultStore(tmp_path / "r.db") as results:
        failed = run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                                results=results, mock_mode=True, continue_on_error=True)
        assert failed == 1
        assert results.well_status("e1", "A1") == "done"
        assert results.well_status("e1", "A2") == "failed"
        assert results.well_status("e1", "A3") == "done"


def test_resume_skips_done(tmp_path):
    exp = _exp(tmp_path)
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        results.start_experiment(exp)
        results.set_well_status("e1", "A1", "done")
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                       results=results, mock_mode=True, resume=True)
    assert [r[0] for r in sharc.client.runs] == ["e1:A2:sharc", "e1:A3:sharc"]
