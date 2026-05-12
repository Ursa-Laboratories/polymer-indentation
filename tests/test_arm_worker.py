"""arm_worker Flask app tests (mock mode — no xArm/rail/SDKs needed)."""

import pytest

pytest.importorskip("flask")

from arm_worker.app import ROUTES, create_app  # noqa: E402


@pytest.fixture
def client():
    app = create_app(mock_mode_default=True)
    app.testing = True
    return app.test_client()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["device"] == "xarm"
    assert body["mock_mode_default"] is True
    assert "opentrons->uv_station" in body["routes"]


def test_known_route_runs_in_mock(client):
    r = client.post("/run", json={"from": "opentrons", "to": "uv_station", "run_id": "t1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body == {"success": True, "from": "opentrons", "to": "uv_station", "run_id": "t1", "mock": True}


def test_all_routes_run_in_mock(client):
    for (src, dst) in ROUTES:
        r = client.post("/run", json={"from": src, "to": dst, "mock_mode": True})
        assert r.status_code == 200, (src, dst, r.get_data(as_text=True))
        assert r.get_json()["success"] is True


def test_unknown_route_400(client):
    r = client.post("/run", json={"from": "asmi", "to": "mars"})
    assert r.status_code == 400
    assert "no route" in r.get_json()["error"]


def test_missing_fields_400(client):
    assert client.post("/run", json={"from": "asmi"}).status_code == 400


def test_busy_returns_409(monkeypatch):
    """While one transfer holds the hardware lock, a second /run gets 409."""
    import threading

    import arm_worker.app as mod

    # Strip the settle delays so the mock transfer is instant except for our gate.
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)

    started, release = threading.Event(), threading.Event()
    real_set_position = mod._MockArm.set_position
    calls = {"n": 0}

    def _maybe_block(self, *pos, **kw):
        calls["n"] += 1
        if calls["n"] == 1:           # block on the first arm move so the lock stays held
            started.set()
            release.wait(timeout=10)
        return real_set_position(self, *pos, **kw)

    monkeypatch.setattr(mod._MockArm, "set_position", _maybe_block)

    app = create_app(mock_mode_default=True)
    app.testing = True
    result = {}

    def _fire():
        result["first"] = app.test_client().post(
            "/run", json={"from": "opentrons", "to": "uv_station", "run_id": "a"}
        ).status_code

    t = threading.Thread(target=_fire)
    t.start()
    assert started.wait(timeout=5), "first transfer never started"

    second = app.test_client().post("/run", json={"from": "asmi", "to": "uv_station", "run_id": "b"})
    assert second.status_code == 409
    assert "already running" in second.get_json()["error"]

    release.set()
    t.join(timeout=5)
    assert result["first"] == 200
