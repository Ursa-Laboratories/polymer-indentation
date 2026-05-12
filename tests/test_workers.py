"""Tests for the SSH/local worker management (polymer_indent/workers.py)."""

import subprocess
import sys
import types

import pytest

from polymer_indent import workers as W


def _cfg(raw):
    return types.SimpleNamespace(raw=raw)


SAMPLE = {
    "stations": {
        "sharc": {"base_url": "http://sharc:8000",
                  "ssh": {"host": "10.0.0.12", "user": "sartorius-scale",
                          "repo_dir": "~/polymer_indent", "python": ".venv/bin/python",
                          "station_config": "configs/stations/sharc.yaml"}},
        "asmi": {"base_url": "http://asmi:8000"},   # no ssh: block -> health-only
    },
    "arm": {"base_url": "http://arm:5004"},
}


def test_workers_from_config_builds_expected_handles():
    handles = {h.name: h for h in W.workers_from_config(_cfg(SAMPLE))}
    assert isinstance(handles["sharc"], W.SshWorker)
    assert isinstance(handles["asmi"], W._HealthOnly)
    assert isinstance(handles["arm"], W.LocalWorker)
    # arm command defaults to launching this python's `-m arm_worker --port 5004`
    assert handles["arm"].command[:3] == [sys.executable, "-m", "arm_worker"]
    assert handles["arm"].command[-2:] == ["--port", "5004"]


def test_workers_from_config_filter():
    handles = W.workers_from_config(_cfg(SAMPLE), names=["asmi"])
    assert [h.name for h in handles] == ["asmi"]


def test_health_only_cannot_be_managed():
    h, = W.workers_from_config(_cfg(SAMPLE), names=["asmi"])
    with pytest.raises(RuntimeError, match="no 'ssh:' block"):
        h.start()


def test_ssh_worker_remote_commands(monkeypatch):
    w = W.SshWorker(name="sharc", base_url="http://sharc:8000", host="h", user="u",
                    repo_dir="~/polymer_indent", station_config="configs/stations/sharc.yaml",
                    python=".venv/bin/python")
    seen = []
    monkeypatch.setattr(w, "_ssh", lambda cmd, **_k: (seen.append(cmd) or
                        subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")))
    monkeypatch.setattr(w, "is_up", lambda: False)

    w.start(wait=False)
    assert "cd ~/polymer_indent &&" in seen[-1]
    assert "setsid .venv/bin/python -m station_worker --config configs/stations/sharc.yaml" in seen[-1]
    assert ">> worker.log 2>&1 < /dev/null" in seen[-1]

    monkeypatch.setattr(w, "remote_processes", lambda: "12345 python -m station_worker --config configs/stations/sharc.yaml")
    w.stop(wait=False)
    assert seen[-1] == 'pkill -f "station_worker --config configs/stations/sharc.yaml" || true'

    w.logs(20)
    assert seen[-1].startswith("tail -n 20 ~/polymer_indent/worker.log")


def test_ssh_worker_start_noop_when_up(monkeypatch):
    w = W.SshWorker(name="x", base_url="http://x:8000", host="h", user="u",
                    repo_dir="~/r", station_config="configs/stations/x.yaml")
    monkeypatch.setattr(w, "is_up", lambda: True)
    monkeypatch.setattr(w, "_ssh", lambda *a, **k: pytest.fail("should not ssh when already up"))
    assert w.start() == "already running"


def test_ssh_worker_start_raises_on_ssh_failure(monkeypatch):
    w = W.SshWorker(name="x", base_url="http://x:8000", host="h", user="u",
                    repo_dir="~/r", station_config="configs/stations/x.yaml")
    monkeypatch.setattr(w, "is_up", lambda: False)
    monkeypatch.setattr(w, "_ssh", lambda *a, **k: subprocess.CompletedProcess(
        args=[], returncode=255, stdout="", stderr="Permission denied (publickey)."))
    with pytest.raises(RuntimeError, match="ssh launch failed"):
        w.start(wait=False)


def test_health_returns_none_when_unreachable():
    assert W._health("http://127.0.0.1:1", timeout=0.3) is None


def test_local_worker_start_noop_when_up(tmp_path, monkeypatch):
    lw = W.LocalWorker(name="arm", base_url="http://arm:5004",
                       command=[sys.executable, "-c", "pass"],
                       log_path=tmp_path / "a.log", pid_path=tmp_path / "a.pid")
    monkeypatch.setattr(lw, "is_up", lambda: True)
    assert lw.start() == "already running"


def test_local_worker_stop_when_nothing_running(tmp_path, monkeypatch):
    lw = W.LocalWorker(name="arm", base_url="http://arm:5004", command=["x"],
                       log_path=tmp_path / "a.log", pid_path=tmp_path / "a.pid")
    monkeypatch.setattr(lw, "is_up", lambda: False)
    assert lw.stop() == "not running"


def test_cmd_workers_status_against_live_server(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    import threading

    from werkzeug.serving import make_server

    from arm_worker.app import create_app
    from polymer_indent.cli import cmd_workers

    monkeypatch.setattr(W, "_RUN_DIR", tmp_path / ".run")  # keep the repo clean
    srv = make_server("127.0.0.1", 0, create_app(mock_mode_default=True))
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = tmp_path / "controller.yaml"
        cfg.write_text(
            "stations:\n"
            "  sharc: {base_url: 'http://127.0.0.1:1'}\n"
            "  asmi: {base_url: 'http://127.0.0.1:1'}\n"
            f"arm:\n  base_url: 'http://127.0.0.1:{port}'\n"
        )
        rc = cmd_workers(types.SimpleNamespace(action="status", devices=["arm"],
                                               lines=10, config=str(cfg), verbose=False))
        assert rc == 0
        rc = cmd_workers(types.SimpleNamespace(action="status", devices=["asmi"],
                                               lines=10, config=str(cfg), verbose=False))
        assert rc == 1   # nothing on 127.0.0.1:1
    finally:
        srv.shutdown()
