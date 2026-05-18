from main_bioadhesives_workcell import load_transfers


def test_load_transfers_from_pilot_protocol(repo_root):
    assert load_transfers(repo_root / "opentrons_bioadhesives_pilot.py") == [
        ("A1", "A1"),
        ("A1", "A2"),
        ("A1", "A3"),
        ("B1", "B1"),
        ("B1", "B2"),
        ("B1", "B3"),
    ]
