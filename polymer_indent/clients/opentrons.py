"""Opentrons client — PLACEHOLDER.

The real workcell fills a well on an Opentrons Flex (HTTP REST: upload protocol
-> create run -> play -> poll). That integration is out of scope for this first
cut; this client just logs the requested fill and returns a success-shaped dict
so the loop runs end to end. The real implementation goes where the TODO is.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

log = logging.getLogger("polymer_indent.opentrons")


class OpentronsClient:
    def __init__(self, base_url: str | None = None, *, timeout_s: float = 600.0):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout_s = timeout_s

    def health(self) -> Dict[str, Any]:
        # Placeholder: report degraded so it's obvious this isn't wired up.
        return {"status": "placeholder", "device": "opentrons", "base_url": self.base_url}

    def run_fill(
        self,
        *,
        well: str,
        volume_ul: float,
        formulation: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispense ``volume_ul`` of ``formulation`` into ``well``.

        PLACEHOLDER: does not touch hardware.
        """
        log.warning(
            "OpentronsClient.run_fill is a PLACEHOLDER — "
            "well=%s volume_ul=%s formulation=%s run_id=%s (no hardware)",
            well, volume_ul, formulation, run_id,
        )
        # TODO(opentrons): replace with the real Flex REST flow:
        #   1. POST {base_url}/protocols       (templated .py + labware .json)
        #   2. POST {base_url}/runs            {"data": {"protocolId": ...}}
        #   3. POST {base_url}/runs/{id}/actions  {"data": {"actionType": "play"}}
        #   4. poll GET {base_url}/runs/{id}   until status in {succeeded, failed}
        return {
            "success": True,
            "placeholder": True,
            "well": well,
            "volume_dispensed": volume_ul,
            "formulation": formulation,
            "run_id": run_id,
            "timestamp": time.time(),
        }

    def stop(self) -> Dict[str, Any]:
        log.warning("OpentronsClient.stop is a PLACEHOLDER (no hardware)")
        return {"success": True, "placeholder": True}


__all__ = ["OpentronsClient"]
