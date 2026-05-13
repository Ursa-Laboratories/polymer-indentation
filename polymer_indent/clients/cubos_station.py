"""Client for a CubOS station Pi (SHARC / ASMI) running ``station_worker``.

The Pi has cubos installed locally. Each ``run_protocol`` call sends the frozen
gantry + deck YAML plus the (well-swapped) protocol YAML; the Pi writes the
three files, runs ``cubos.setup_protocol`` -> ``protocol.run``, and returns the
results plus artifact paths.

API (see ``station_worker.app``):
    GET  /health
    POST /validate-protocol   {protocol_yaml, gantry_config?, deck_config?, mock_mode?}
    POST /run-protocol        {run_id, gantry_config, deck_config, protocol_yaml, mock_mode, metadata?}
    POST /stop
    GET  /runs/<run_id>
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

from ._http import HttpError, get_json, new_session, post_json

log = logging.getLogger("polymer_indent.cubos_station")


class StationRunError(RuntimeError):
    """A station accepted the request but the protocol run failed."""

    def __init__(self, station: str, run_id: str, payload: Dict[str, Any]):
        self.station = station
        self.run_id = run_id
        self.payload = payload
        msg = payload.get("error") or "run failed (no error message)"
        super().__init__(f"[{station}] run {run_id!r} failed: {msg}")


class CubOSStationClient:
    def __init__(
        self,
        base_url: str,
        station: str,
        *,
        gantry_config_yaml: str,
        deck_config_yaml: str,
        timeout_s: float = 900.0,
        mock_mode: bool = False,
        session: Any | None = None,
    ):
        """
        Args:
            base_url: e.g. ``"http://10.210.29.12:8000"``.
            station: short name for logging / error messages ("sharc" / "asmi").
            gantry_config_yaml: the frozen gantry YAML *text* sent every run.
            deck_config_yaml: the frozen deck YAML *text* sent every run.
            timeout_s: read timeout for ``run_protocol`` (covers the whole run).
            mock_mode: default ``mock_mode`` sent when a call doesn't override it.
        """
        self.base_url = base_url.rstrip("/")
        self.station = station
        self.gantry_config_yaml = gantry_config_yaml
        self.deck_config_yaml = deck_config_yaml
        self.timeout_s = timeout_s
        self.mock_mode = mock_mode
        self._session = session or new_session()

    # -- endpoints -------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return get_json(self._session, f"{self.base_url}/health", timeout=15.0)

    def validate_protocol(self, protocol_yaml: str) -> Dict[str, Any]:
        """Offline validation on the Pi (cubos setup_protocol, no hardware)."""
        return post_json(
            self._session,
            f"{self.base_url}/validate-protocol",
            {
                "protocol_yaml": protocol_yaml,
                "gantry_config": self.gantry_config_yaml,
                "deck_config": self.deck_config_yaml,
            },
            timeout=60.0,
        )

    def run_protocol(
        self,
        *,
        run_id: str,
        protocol_yaml: str,
        metadata: Optional[Dict[str, Any]] = None,
        mock_mode: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Run one protocol on the station. Returns the worker's response dict.

        Raises:
            HttpError: transport / non-2xx (incl. 409 if the station is busy).
            StationRunError: HTTP 200 but ``success`` is false.
        """
        mode = self.mock_mode if mock_mode is None else mock_mode
        payload = {
            "run_id": run_id,
            "gantry_config": self.gantry_config_yaml,
            "deck_config": self.deck_config_yaml,
            "protocol_yaml": protocol_yaml,
            "mock_mode": mode,
        }
        if metadata:
            payload["metadata"] = metadata

        log.info("[%s] run-protocol run_id=%s mock=%s", self.station, run_id, mode)
        resp = post_json(
            self._session,
            f"{self.base_url}/run-protocol",
            payload,
            timeout=self.timeout_s,
        )
        if not resp.get("success", False):
            raise StationRunError(self.station, run_id, resp)
        return resp

    def get_run(self, run_id: str) -> Dict[str, Any]:
        return get_json(
            self._session,
            f"{self.base_url}/runs/{quote(run_id, safe='')}",
            timeout=15.0,
        )

    def stop(self) -> Dict[str, Any]:
        return post_json(self._session, f"{self.base_url}/stop", {}, timeout=15.0)


__all__ = ["CubOSStationClient", "StationRunError", "HttpError"]
