import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from polymer_indent.clients.opentrons import OpentronsClient, OpentronsRunError


class _OpentronsHandler(BaseHTTPRequestHandler):
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        state = self.server.state
        if self.path == "/health":
            self._json({"status": "ok", "device": "opentrons"})
            return
        if self.path == "/runs/run-1":
            self._json({"data": {"id": "run-1", "status": state["status"], "errors": []}})
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self):
        state = self.server.state
        if not self.headers.get("opentrons-version"):
            self._json({"error": "missing opentrons-version"}, status=422)
            return
        body = self._read_body()
        if self.path == "/protocols":
            state["uploads"].append(body.decode("utf-8", errors="replace"))
            self._json({"data": {"id": "protocol-1"}}, status=201)
            return
        if self.path == "/runs":
            state["runs"].append(json.loads(body.decode("utf-8")))
            self._json({"data": {"id": "run-1", "status": "idle"}}, status=201)
            return
        if self.path == "/runs/run-1/actions":
            state["actions"].append(json.loads(body.decode("utf-8")))
            self._json({"data": {"id": "action-1", "actionType": "play"}}, status=201)
            return
        self._json({"error": "not found"}, status=404)


class _Server:
    def __init__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _OpentronsHandler)
        self.httpd.state = {"uploads": [], "runs": [], "actions": [], "status": "succeeded"}
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def state(self):
        return self.httpd.state

    def stop(self):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()


@pytest.fixture
def opentrons_server():
    srv = _Server()
    try:
        yield srv
    finally:
        srv.stop()


def test_opentrons_run_fill_uploads_plays_and_polls(opentrons_server):
    client = OpentronsClient(opentrons_server.url, timeout_s=2.0, poll_interval_s=0.01)
    resp = client.run_fill(well="A2", source_well="A1", volume_ul=100, run_id="e1:A2:fill")

    assert resp["success"] is True
    assert resp["opentrons_protocol_id"] == "protocol-1"
    assert resp["opentrons_run_id"] == "run-1"
    assert resp["source_well"] == "A1"
    assert resp["well"] == "A2"
    assert 'stock_rack["A1"]' in opentrons_server.state["uploads"][0]
    assert 'plate["A2"]' in opentrons_server.state["uploads"][0]
    assert opentrons_server.state["runs"] == [{"data": {"protocolId": "protocol-1"}}]
    assert opentrons_server.state["actions"] == [{"data": {"actionType": "play"}}]


def test_opentrons_run_fill_raises_on_failed_run(opentrons_server):
    opentrons_server.state["status"] = "failed"
    client = OpentronsClient(opentrons_server.url, timeout_s=2.0, poll_interval_s=0.01)

    with pytest.raises(OpentronsRunError):
        client.run_fill(well="A2", source_well="A1", volume_ul=100)
