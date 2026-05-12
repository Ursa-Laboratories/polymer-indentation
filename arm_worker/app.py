"""Flask app for the xArm + Vention-rail plate-transfer worker.

  GET  /health
  POST /run    {"from": "<loc>", "to": "<loc>", "run_id"?: "...", "mock_mode"?: bool}
  POST /stop

One transfer at a time (a process lock; busy -> 409). ``mock_mode`` (per request,
or the worker default) runs the same pick/place sequence against logging-only
stand-ins — no xArm, no rail, no SDKs imported.

Refactored from denos workers/arm_rail_worker/arm_worker.py.
"""

from __future__ import annotations

import logging
import threading
import time

from flask import Flask, jsonify, request

from . import positions as P

log = logging.getLogger("arm_worker")

# YAML location names -> sequence of pick/place steps. Filled in below once the
# step functions are defined.
ROUTES: dict = {}


# --------------------------------------------------------------------------- mock stand-ins

class _MockArm:
    """Logging-only stand-in for xarm.wrapper.XArmAPI (+ its ._arm gripper API)."""

    def __init__(self):
        self._arm = self

    def connect(self): pass
    def clean_error(self): pass
    def clean_warn(self): pass
    def motion_enable(self, *_a, **_k): pass
    def set_mode(self, *_a, **_k): pass
    def set_state(self, *_a, **_k): pass
    def get_state(self): return (0, 2)

    def set_position(self, *pos, **_k):
        log.info("[mock arm] move -> %s", list(pos[:3]))
        return 0

    def open_lite6_gripper(self): log.info("[mock arm] gripper open")
    def close_lite6_gripper(self): log.info("[mock arm] gripper close")
    def stop_lite6_gripper(self): pass


class _MockActuator:
    def home(self): log.info("[mock rail] home")
    def wait_for_move_completion(self, **_k): pass
    def stop(self): pass


class _MockRail:
    """Logging-only stand-in for device_drivers.vention_railway.VentionRailway."""

    def __init__(self):
        self.actuator = _MockActuator()

    def move_absolute(self, mm, **_k):
        log.info("[mock rail] move_absolute -> %s mm", mm)


# --------------------------------------------------------------------------- the app

def create_app(*, mock_mode_default: bool = False, arm_ip: str = P.ARM_IP,
               rail_ip: str = P.RAIL_IP, ot_plate_type: str = P.OT_PLATE_TYPE) -> Flask:
    app = Flask(__name__)

    state = {"busy": False, "current": None, "arm": None, "rail": None, "hardware_is_mock": None}
    hw_lock = threading.Lock()
    stop_event = threading.Event()

    def _check_stop():
        if stop_event.is_set():
            raise RuntimeError("stopped by operator")

    def _is_real(obj) -> bool:
        return not isinstance(obj, (_MockArm, _MockRail, _MockActuator))

    def _grip_settle(arm):
        if _is_real(arm):
            time.sleep(1.0)

    # -- hardware --------------------------------------------------------

    def _get_hardware(mock: bool):
        if state["arm"] is not None and state["hardware_is_mock"] == mock:
            return state["arm"], state["rail"]
        if mock:
            log.info("using MOCK arm + rail (no hardware)")
            state["arm"], state["rail"] = _MockArm(), _MockRail()
        else:
            log.info("connecting to xArm (%s) and Vention rail (%s)...", arm_ip, rail_ip)
            from xarm.wrapper import XArmAPI  # noqa: PLC0415
            try:
                from device_drivers.vention_railway import VentionRailway  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "VentionRailway not importable — put the keeper_pc repo (its "
                    "device_drivers/) on PYTHONPATH, and `pip install machine-logic-sdk` "
                    f"(Python 3.10). Original error: {exc}"
                ) from exc
            arm = XArmAPI(arm_ip)
            arm.connect()
            arm.clean_error()
            arm.clean_warn()
            arm.motion_enable(True)
            arm.set_mode(0)
            arm.set_state(0)
            time.sleep(0.5)
            rail = VentionRailway(ip=rail_ip)
            log.info("homing rail...")
            rail.actuator.home()
            rail.actuator.wait_for_move_completion(timeout=60)
            state["arm"], state["rail"] = arm, rail
        state["hardware_is_mock"] = mock
        return state["arm"], state["rail"]

    # -- low-level move helpers -----------------------------------------

    def _move(arm, pos, speed=P.ARM_SPEED):
        _check_stop()
        code = arm.set_position(*pos, speed=speed, acc=100, wait=True, motion_type=0)
        _check_stop()
        if code != 0:
            raise RuntimeError(f"set_position failed: code={code}, pos={pos}")
        if _is_real(arm):
            time.sleep(0.5)

    def _rail_move(rail, mm):
        _check_stop()
        rail.move_absolute(mm, timeout=P.RAIL_TIMEOUT, speed=100)
        if _is_real(rail):
            time.sleep(0.5)

    def _ot_positions():
        return P.ot_positions(ot_plate_type)

    # -- pick / place sequences -----------------------------------------

    def pick_from_opentrons(arm, rail):
        pickup, lifted = _ot_positions()
        log.info("pick_from_opentrons (%s plate)", ot_plate_type)
        arm._arm.open_lite6_gripper(); _grip_settle(arm)
        _move(arm, P.ARM_SAFE_POSITION, speed=100)
        rail.actuator.home(); rail.actuator.wait_for_move_completion(timeout=60)
        _move(arm, lifted)
        _move(arm, pickup)
        arm._arm.close_lite6_gripper(); _grip_settle(arm)
        _move(arm, lifted)
        _move(arm, P.ARM_SAFE_POSITION)

    def place_at_opentrons(arm, rail):
        pickup, lifted = _ot_positions()
        log.info("place_at_opentrons (%s plate)", ot_plate_type)
        _move(arm, P.ARM_SAFE_POSITION, speed=100)
        rail.actuator.home(); rail.actuator.wait_for_move_completion(timeout=60)
        _move(arm, lifted)
        _move(arm, pickup)
        arm._arm.open_lite6_gripper(); _grip_settle(arm)
        _move(arm, lifted)
        _move(arm, P.ARM_SAFE_POSITION)
        arm._arm.stop_lite6_gripper()
        rail.actuator.home(); rail.actuator.wait_for_move_completion(timeout=60)

    def pick_from_uv(arm, rail):
        log.info("pick_from_uv")
        arm._arm.open_lite6_gripper(); _grip_settle(arm)
        _move(arm, P.ARM_SAFE_POSITION, speed=100)
        _rail_move(rail, P.UV_RAIL_POSITION_MM)
        _move(arm, P.UV_PICKUP_LIFTED)
        _move(arm, P.UV_PICKUP_POSITION)
        arm._arm.close_lite6_gripper(); _grip_settle(arm)
        _move(arm, P.UV_PICKUP_LIFTED)
        _move(arm, P.ARM_SAFE_POSITION)

    def place_at_uv(arm, rail):
        log.info("place_at_uv")
        _move(arm, P.ARM_SAFE_POSITION, speed=100)
        _rail_move(rail, P.UV_RAIL_POSITION_MM)
        _move(arm, P.UV_PICKUP_LIFTED)
        _move(arm, P.UV_PICKUP_POSITION)
        arm._arm.open_lite6_gripper(); _grip_settle(arm)
        _move(arm, P.UV_PICKUP_LIFTED)
        _move(arm, P.ARM_SAFE_POSITION)
        arm._arm.stop_lite6_gripper()
        rail.actuator.home(); rail.actuator.wait_for_move_completion(timeout=60)

    def pick_from_asmi(arm, rail):
        log.info("pick_from_asmi")
        arm._arm.open_lite6_gripper(); _grip_settle(arm)
        _move(arm, P.ARM_SAFE_POSITION, speed=100)
        _rail_move(rail, P.ASMI_RAIL_POSITION_MM)
        _move(arm, P.ASMI_SLIDE_IN_LIFTED)
        _move(arm, P.ASMI_SLIDE_IN_POSITION)
        arm._arm.close_lite6_gripper(); _grip_settle(arm)
        _move(arm, P.ASMI_SLIDE_OUT_POSITION)
        _move(arm, P.ASMI_SLIDE_OUT_LIFTED)
        _move(arm, P.ARM_SAFE_POSITION)

    def place_at_asmi(arm, rail):
        log.info("place_at_asmi")
        _move(arm, P.ARM_SAFE_POSITION, speed=100)
        _rail_move(rail, P.ASMI_RAIL_POSITION_MM)
        _move(arm, P.ASMI_SLIDE_OUT_LIFTED)
        _move(arm, P.ASMI_SLIDE_OUT_POSITION)
        _move(arm, P.ASMI_SLIDE_IN_POSITION)
        arm._arm.open_lite6_gripper(); _grip_settle(arm)
        _move(arm, P.ASMI_SLIDE_IN_PUSH)
        _move(arm, P.ASMI_SLIDE_IN_POSITION)
        _move(arm, P.ASMI_SLIDE_IN_LIFTED)
        _move(arm, P.ARM_SAFE_POSITION)
        arm._arm.stop_lite6_gripper()
        rail.actuator.home(); rail.actuator.wait_for_move_completion(timeout=60)

    routes = {
        ("opentrons", "uv_station"):  lambda a, r: (pick_from_opentrons(a, r), place_at_uv(a, r)),
        ("uv_station", "asmi"):       lambda a, r: (pick_from_uv(a, r),        place_at_asmi(a, r)),
        ("asmi", "uv_station"):       lambda a, r: (pick_from_asmi(a, r),      place_at_uv(a, r)),
        ("asmi", "opentrons"):        lambda a, r: (pick_from_asmi(a, r),      place_at_opentrons(a, r)),
        ("asmi", "storage_end"):      lambda a, r: (pick_from_asmi(a, r),      place_at_opentrons(a, r)),
        ("opentrons", "storage_end"): lambda a, r: (pick_from_opentrons(a, r), place_at_opentrons(a, r)),
    }
    ROUTES.update(routes)  # exported for tests / introspection

    # -- routes ----------------------------------------------------------

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "device": "xarm",
            "mock_mode_default": mock_mode_default,
            "busy": state["busy"],
            "current": state["current"],
            "routes": [f"{a}->{b}" for a, b in routes],
        })

    @app.post("/stop")
    def stop():
        log.warning("STOP requested")
        stop_event.set()
        arm = state["arm"]
        if arm is not None and not isinstance(arm, _MockArm):
            try:
                arm.set_state(4)
            except Exception as exc:  # noqa: BLE001
                log.warning("xArm stop error: %s", exc)
            try:
                arm._arm.stop_lite6_gripper()
            except Exception:  # noqa: BLE001
                pass
        rail = state["rail"]
        if rail is not None:
            try:
                rail.actuator.stop()
            except Exception:  # noqa: BLE001
                pass
        return jsonify({"stopped": True, "device": "xarm", "busy": state["busy"]})

    @app.post("/run")
    def run():
        if not hw_lock.acquire(blocking=False):
            return jsonify({"success": False, "error": "already running a transfer",
                            "current": state["current"]}), 409
        stop_event.clear()
        from_loc = to_loc = None
        try:
            data = request.get_json(silent=True) or {}
            from_loc, to_loc = data.get("from"), data.get("to")
            run_id = data.get("run_id")
            mock = bool(data.get("mock_mode", mock_mode_default))
            if not from_loc or not to_loc:
                return jsonify({"success": False, "error": "missing 'from' or 'to'"}), 400
            route_fn = routes.get((from_loc, to_loc))
            if route_fn is None:
                supported = ", ".join(f"{a}->{b}" for a, b in routes)
                return jsonify({"success": False,
                                "error": f"no route for '{from_loc}' -> '{to_loc}'; supported: {supported}"}), 400

            state["busy"] = True
            state["current"] = run_id or f"{from_loc}->{to_loc}"
            log.info("transfer %s -> %s (run_id=%s, mock=%s)", from_loc, to_loc, run_id, mock)

            arm, rail = _get_hardware(mock)
            if not isinstance(arm, _MockArm):
                arm.clean_error(); arm.clean_warn(); arm.motion_enable(True)
                arm.set_mode(0); arm.set_state(0)
                for _ in range(40):
                    time.sleep(0.5)
                    if arm.get_state()[1] == 2:
                        break
            _check_stop()
            route_fn(arm, rail)
            _check_stop()
            log.info("transfer complete: %s -> %s", from_loc, to_loc)
            return jsonify({"success": True, "from": from_loc, "to": to_loc,
                            "run_id": run_id, "mock": mock})
        except Exception as exc:  # noqa: BLE001 — report, don't crash
            if stop_event.is_set():
                return jsonify({"success": False, "error": "stopped by operator",
                                "from": from_loc, "to": to_loc})
            log.exception("transfer failed: %s -> %s", from_loc, to_loc)
            return jsonify({"success": False, "error": str(exc), "from": from_loc, "to": to_loc}), 500
        finally:
            state["busy"] = False
            state["current"] = None
            hw_lock.release()

    return app


__all__ = ["create_app", "ROUTES"]
