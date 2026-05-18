"""Opentrons Flex client.

When ``base_url`` is configured, the client uploads a generated one-well
protocol to the Flex HTTP API, creates a run, plays it, and polls until the run
finishes. With no ``base_url`` it retains the original placeholder behavior so
offline tests and controller dry runs can still exercise the full workcell loop.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from ._http import HttpError, get_json, new_session

log = logging.getLogger("polymer_indent.opentrons")

_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "stopped",
    "canceled",
    "cancelled",
    "blocked-by-open-door",
}


class OpentronsRunError(RuntimeError):
    def __init__(self, run_id: str | None, payload: Dict[str, Any]):
        self.run_id = run_id
        self.payload = payload
        status = _run_status(payload) or "unknown"
        errors = payload.get("data", {}).get("errors") if isinstance(payload.get("data"), dict) else None
        super().__init__(f"opentrons run {run_id or '<unknown>'} ended with status {status}: {errors or payload!r}")


def render_viscous_fill_protocol(
    *,
    source_well: str,
    target_well: str,
    volume_ul: float,
    flow_rate_ul_min: float = 150.0,
    air_expulsion_ul: float = 20.0,
    tip_lift_height_mm: float = 8.0,
) -> str:
    """Return a one-transfer Flex protocol derived from the pilot script."""
    source_well = _normalize_well(source_well)
    target_well = _normalize_well(target_well)
    return f'''from opentrons import protocol_api

metadata = {{
    "apiLevel": "2.15",
    "protocolName": "Bioadhesives viscous one-well dispense",
    "author": "polymer_indent",
    "description": "Generated one-transfer viscous reagent protocol for the polymer indentation workcell.",
}}

requirements = {{"robotType": "Flex"}}

custom_tube_rack = {{
    "ordering": [["A1", "B1"], ["A2", "B2"], ["A3", "B3"]],
    "brand": {{"brand": "Custom", "brandId": []}},
    "metadata": {{
        "displayName": "Custom 6 Tube Rack with Generic 20 mL",
        "displayCategory": "tubeRack",
        "displayVolumeUnits": "µL",
        "tags": []
    }},
    "dimensions": {{"xDimension": 127, "yDimension": 85, "zDimension": 135}},
    "wells": {{
        "A1": {{"depth": 58, "totalLiquidVolume": 20000, "shape": "circular", "diameter": 30, "x": 25, "y": 62, "z": 65}},
        "B1": {{"depth": 58, "totalLiquidVolume": 20000, "shape": "circular", "diameter": 30, "x": 25, "y": 22, "z": 65}},
        "A2": {{"depth": 58, "totalLiquidVolume": 20000, "shape": "circular", "diameter": 30, "x": 65, "y": 62, "z": 65}},
        "B2": {{"depth": 58, "totalLiquidVolume": 20000, "shape": "circular", "diameter": 30, "x": 65, "y": 22, "z": 65}},
        "A3": {{"depth": 58, "totalLiquidVolume": 20000, "shape": "circular", "diameter": 30, "x": 105, "y": 62, "z": 65}},
        "B3": {{"depth": 58, "totalLiquidVolume": 20000, "shape": "circular", "diameter": 30, "x": 105, "y": 22, "z": 65}}
    }},
    "groups": [{{
        "brand": {{"brand": "Generic", "brandId": []}},
        "metadata": {{"wellBottomShape": "flat", "displayCategory": "tubeRack"}},
        "wells": ["A1", "B1", "A2", "B2", "A3", "B3"]
    }}],
    "parameters": {{
        "format": "irregular",
        "quirks": [],
        "isTiprack": "False",
        "isMagneticModuleCompatible": "False",
        "loadName": "jeremy_custom_6_tube_rack_20ml"
    }},
    "namespace": "custom_beta",
    "version": 1,
    "schemaVersion": 2,
    "cornerOffsetFromSlot": {{"x": 0, "y": 0, "z": 0}}
}}


def run(protocol: protocol_api.ProtocolContext):
    tips = protocol.load_labware("opentrons_flex_96_tiprack_1000ul", "A2")
    stock_rack = protocol.load_labware_from_definition(custom_tube_rack, "B2")
    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", "D2")
    p1000 = protocol.load_instrument("flex_1channel_1000", "right", tip_racks=[tips])

    p1000.flow_rate.aspirate = {float(flow_rate_ul_min)!r}
    p1000.flow_rate.dispense = {float(flow_rate_ul_min)!r}

    protocol.comment("Starting generated viscous transfer: {source_well} -> {target_well}")
    p1000.pick_up_tip()
    p1000.aspirate({float(volume_ul)!r}, stock_rack["{source_well}"].bottom(z=5))
    p1000.dispense({float(volume_ul)!r}, plate["{target_well}"].bottom(z=5))
    p1000.move_to(plate["{target_well}"].bottom(z={float(tip_lift_height_mm)!r}))
    p1000.dispense({float(air_expulsion_ul)!r})
    p1000.drop_tip()
    protocol.comment("Generated viscous transfer completed")
'''


class OpentronsClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float = 600.0,
        poll_interval_s: float = 2.0,
        api_version: str = "*",
        session: Any | None = None,
    ):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self.api_version = api_version
        self._session = session or new_session()

    def health(self) -> Dict[str, Any]:
        if not self.base_url:
            return {"status": "placeholder", "device": "opentrons", "base_url": self.base_url}
        try:
            return get_json(self._session, f"{self.base_url}/health", timeout=15.0)
        except HttpError:
            return self._get_json("/networking/status", timeout=15.0)

    def run_fill(
        self,
        *,
        well: str,
        volume_ul: float,
        source_well: Optional[str] = None,
        formulation: Optional[str] = None,
        run_id: Optional[str] = None,
        flow_rate_ul_min: float = 150.0,
        air_expulsion_ul: float = 20.0,
        tip_lift_height_mm: float = 8.0,
    ) -> Dict[str, Any]:
        """Dispense ``volume_ul`` of ``formulation`` into ``well``.

        If ``base_url`` is set, this touches Opentrons hardware.
        """
        source_well = source_well or formulation or "A1"
        well = _normalize_well(well)
        source_well = _normalize_well(source_well)
        if self.base_url:
            return self._run_flex_fill(
                well=well,
                source_well=source_well,
                volume_ul=volume_ul,
                formulation=formulation,
                run_id=run_id,
                flow_rate_ul_min=flow_rate_ul_min,
                air_expulsion_ul=air_expulsion_ul,
                tip_lift_height_mm=tip_lift_height_mm,
            )

        log.warning(
            "OpentronsClient.run_fill is a PLACEHOLDER — "
            "source_well=%s well=%s volume_ul=%s formulation=%s run_id=%s (no hardware)",
            source_well, well, volume_ul, formulation, run_id,
        )
        return {
            "success": True,
            "placeholder": True,
            "source_well": source_well,
            "well": well,
            "volume_dispensed": volume_ul,
            "formulation": formulation,
            "run_id": run_id,
            "timestamp": time.time(),
        }

    def _run_flex_fill(
        self,
        *,
        well: str,
        source_well: str,
        volume_ul: float,
        formulation: Optional[str],
        run_id: Optional[str],
        flow_rate_ul_min: float,
        air_expulsion_ul: float,
        tip_lift_height_mm: float,
    ) -> Dict[str, Any]:
        protocol_text = render_viscous_fill_protocol(
            source_well=source_well,
            target_well=well,
            volume_ul=volume_ul,
            flow_rate_ul_min=flow_rate_ul_min,
            air_expulsion_ul=air_expulsion_ul,
            tip_lift_height_mm=tip_lift_height_mm,
        )
        protocol_id = self._upload_protocol(protocol_text, key=run_id)
        robot_run_id = self._create_run(protocol_id)
        self._play_run(robot_run_id)
        final = self._poll_run(robot_run_id)
        success = _run_status(final) == "succeeded"
        payload = {
            "success": success,
            "placeholder": False,
            "source_well": source_well,
            "well": well,
            "volume_dispensed": volume_ul,
            "formulation": formulation,
            "run_id": run_id,
            "opentrons_protocol_id": protocol_id,
            "opentrons_run_id": robot_run_id,
            "status": _run_status(final),
            "final_run": final,
            "timestamp": time.time(),
        }
        if not success:
            raise OpentronsRunError(robot_run_id, final)
        return payload

    def _upload_protocol(self, protocol_text: str, *, key: Optional[str]) -> str:
        files = {
            "files": ("bioadhesives_one_well.py", protocol_text.encode("utf-8"), "text/x-python")
        }
        data = {"key": key} if key else None
        try:
            resp = self._session.post(
                f"{self.base_url}/protocols",
                headers=self._headers(),
                files=files,
                data=data,
                timeout=self.timeout_s,
            )
        except Exception as exc:
            raise HttpError(f"POST {self.base_url}/protocols failed: {exc}") from exc
        body = _decode_response(resp, f"{self.base_url}/protocols")
        protocol_id = _data_id(body)
        if not protocol_id:
            raise HttpError(f"protocol upload response missing data.id: {body!r}")
        return protocol_id

    def _create_run(self, protocol_id: str) -> str:
        body = self._post_json("/runs", {"data": {"protocolId": protocol_id}}, timeout=30.0)
        robot_run_id = _data_id(body)
        if not robot_run_id:
            raise HttpError(f"run creation response missing data.id: {body!r}")
        return robot_run_id

    def _play_run(self, robot_run_id: str) -> None:
        self._post_json(
            f"/runs/{robot_run_id}/actions",
            {"data": {"actionType": "play"}},
            timeout=30.0,
        )

    def _poll_run(self, robot_run_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.timeout_s
        last: Dict[str, Any] = {}
        while time.time() < deadline:
            last = self._get_json(f"/runs/{robot_run_id}", timeout=30.0)
            status = _run_status(last)
            if status in _TERMINAL_STATUSES:
                return last
            time.sleep(self.poll_interval_s)
        raise TimeoutError(f"Opentrons run {robot_run_id} did not finish within {self.timeout_s}s; last={last!r}")

    def _post_json(self, path: str, payload: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.post(url, json=payload, headers=self._headers(), timeout=timeout)
        except Exception as exc:
            raise HttpError(f"POST {url} failed: {exc}") from exc
        return _decode_response(resp, url)

    def _get_json(self, path: str, *, timeout: float) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.get(url, headers=self._headers(), timeout=timeout)
        except Exception as exc:
            raise HttpError(f"GET {url} failed: {exc}") from exc
        return _decode_response(resp, url)

    def _headers(self) -> Dict[str, str]:
        return {"opentrons-version": self.api_version}

    def stop(self) -> Dict[str, Any]:
        log.warning("OpentronsClient.stop is not implemented for Flex runs")
        return {"success": True, "placeholder": not bool(self.base_url)}


def _normalize_well(well: str) -> str:
    value = str(well).strip().upper()
    if not value or not value[0].isalpha() or not value[1:].isdigit():
        raise ValueError(f"not a well id: {well!r}")
    return value


def _decode_response(resp, url: str) -> Dict[str, Any]:
    if resp.status_code >= 400:
        raise HttpError(f"{url} -> HTTP {resp.status_code}: {getattr(resp, 'text', '')[:300]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise HttpError(f"{url} -> non-JSON response: {getattr(resp, 'text', '')[:200]!r}") from exc
    if not isinstance(data, dict):
        raise HttpError(f"{url} -> JSON response is not an object: {data!r}")
    return data


def _data_id(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    return None


def _run_status(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("status"):
        return str(data["status"])
    if payload.get("status"):
        return str(payload["status"])
    return None


__all__ = ["OpentronsClient", "OpentronsRunError", "render_viscous_fill_protocol"]
