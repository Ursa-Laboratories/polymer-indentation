"""arm_worker — xArm Lite 6 + Vention-rail plate-transfer HTTP worker.

Extracted from denos (workers/arm_rail_worker/arm_worker.py). Runs on the
controller box (bear-den-keeper); the main loop / the test scripts POST
``{"from": <location>, "to": <location>}`` to it and it moves the plate between
``opentrons`` / ``uv_station`` / ``asmi`` / ``storage_end``.

  GET  /health
  POST /run    {"from": "...", "to": "...", "run_id"?: "...", "mock_mode"?: bool}
  POST /stop

Real mode talks to the xArm (``xarm-python-sdk``) and the Vention rail
(``device_drivers.vention_railway.VentionRailway`` from the keeper_pc repo —
must be on PYTHONPATH; needs ``machine-logic-sdk`` / Python 3.10). ``--mock``
mode (or ``"mock_mode": true`` in a request) runs the same pick/place sequence
against logging-only stand-ins so the workflow can be exercised from the
controller without any arm hardware.
"""

__version__ = "0.1.0"
