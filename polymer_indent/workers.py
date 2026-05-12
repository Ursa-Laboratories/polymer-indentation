"""Start / stop / inspect the device workers from the controller box.

Station workers run on their Pis — managed over SSH (key-based; an ``ssh:`` block
per station in ``controller.yaml``). The arm worker runs locally on the controller
box — managed as a detached subprocess tracked by a pidfile. "Is it up?" is read
from each worker's ``/health`` endpoint (no SSH needed just to check status).

Uses the local ``ssh`` binary (no paramiko dependency) with ``BatchMode=yes`` —
**no passwords are stored or passed**; configure key-based auth in ``~/.ssh``
(``ssh-copy-id`` to each Pi).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import requests

_RUN_DIR = Path(__file__).resolve().parent.parent / ".run"   # local pidfiles/logs for the arm worker


# --------------------------------------------------------------------------- /health

def _health(base_url: str, timeout: float = 5.0) -> Optional[dict]:
    try:
        r = requests.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


def _wait_health(base_url: str, *, want_up: bool, timeout: float = 20.0, period: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        up = _health(base_url) is not None
        if up == want_up:
            return True
        time.sleep(period)
    return (_health(base_url) is not None) == want_up


# --------------------------------------------------------------------------- SSH-managed station worker

@dataclass
class SshWorker:
    name: str
    base_url: str
    host: str
    user: str
    repo_dir: str                       # where this repo is checked out on the Pi (may use ~)
    station_config: str                 # path to configs/stations/<name>.yaml, relative to repo_dir
    python: str = "python3"             # python to run the worker (relative to repo_dir, or absolute)
    key: Optional[str] = None           # private key path; omit to use ssh's default key/agent
    log: str = "worker.log"             # relative to repo_dir

    # paths in remote commands are left unquoted so the remote shell expands `~`;
    # they come from controller-owned config, so keep them space-free / use absolute or ~-relative.
    @property
    def _pattern(self) -> str:
        return f"station_worker --config {self.station_config}"

    def _ssh(self, remote_cmd: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if self.key:
            argv += ["-i", os.path.expanduser(self.key)]
        argv += [f"{self.user}@{self.host}", remote_cmd]
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    def is_up(self) -> bool:
        return _health(self.base_url) is not None

    def remote_processes(self) -> str:
        r = self._ssh(f'pgrep -af "{self._pattern}" || true')
        return (r.stdout or "").strip()

    def start(self, *, wait: bool = True) -> str:
        if self.is_up():
            return "already running"
        cmd = (
            f"cd {self.repo_dir} && "
            f"( setsid {self.python} -m station_worker --config {self.station_config} "
            f">> {self.log} 2>&1 < /dev/null & ) ; echo launched"
        )
        r = self._ssh(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"ssh launch failed ({self.user}@{self.host}): {r.stderr.strip() or r.stdout.strip()}")
        if wait and not _wait_health(self.base_url, want_up=True, timeout=20.0):
            tail = self._ssh(f"tail -n 30 {self.repo_dir}/{self.log} 2>/dev/null || true").stdout
            raise RuntimeError(f"worker '{self.name}' did not come up at {self.base_url}\n--- {self.log} tail ---\n{tail}")
        return "started"

    def stop(self, *, wait: bool = True) -> str:
        if not self.is_up() and not self.remote_processes():
            return "not running"
        self._ssh(f'pkill -f "{self._pattern}" || true')
        if wait:
            _wait_health(self.base_url, want_up=False, timeout=10.0)
        return "stopped"

    def logs(self, lines: int) -> str:
        r = self._ssh(f"tail -n {int(lines)} {self.repo_dir}/{self.log} 2>/dev/null || echo '(no log file yet)'")
        return r.stdout or r.stderr


# --------------------------------------------------------------------------- locally-managed arm worker

@dataclass
class LocalWorker:
    name: str
    base_url: str
    command: List[str]                  # e.g. [sys.executable, "-m", "arm_worker", "--port", "5004"]
    log_path: Path
    pid_path: Path

    def is_up(self) -> bool:
        return _health(self.base_url) is not None

    def _pid(self) -> Optional[int]:
        try:
            return int(self.pid_path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def start(self, *, wait: bool = True) -> str:
        if self.is_up():
            return "already running"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log_path, "ab")
        proc = subprocess.Popen(
            self.command, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, cwd=str(_RUN_DIR.parent),
        )
        self.pid_path.write_text(str(proc.pid))
        if wait and not _wait_health(self.base_url, want_up=True, timeout=15.0):
            raise RuntimeError(f"arm worker did not come up at {self.base_url} — see {self.log_path}")
        return "started"

    def stop(self, *, wait: bool = True) -> str:
        pid = self._pid()
        if pid is None and not self.is_up():
            return "not running"
        if pid is not None:
            try:
                os.kill(pid, 15)  # SIGTERM
            except ProcessLookupError:
                pass
        if wait:
            _wait_health(self.base_url, want_up=False, timeout=10.0)
        self.pid_path.unlink(missing_ok=True)
        return "stopped"

    def logs(self, lines: int) -> str:
        if not self.log_path.exists():
            return "(no log file yet)"
        data = self.log_path.read_text(errors="replace").splitlines()
        return "\n".join(data[-int(lines):])


# --------------------------------------------------------------------------- build from controller config

def workers_from_config(cfg, names: Optional[List[str]] = None) -> "list":
    """Build worker handles from a ControllerConfig.

    Stations get an :class:`SshWorker` if they have an ``ssh:`` block (else they
    are skipped for SSH ops, but still appear in ``status`` via /health). The arm
    gets a :class:`LocalWorker`.
    """
    import sys

    raw = cfg.raw
    out = []
    wanted = set(names) if names else None

    for st_name, st in (raw.get("stations") or {}).items():
        if wanted and st_name not in wanted:
            continue
        ssh = st.get("ssh") or {}
        if ssh:
            out.append(SshWorker(
                name=st_name, base_url=st["base_url"],
                host=ssh["host"], user=ssh["user"], repo_dir=ssh["repo_dir"],
                station_config=ssh.get("station_config", f"configs/stations/{st_name}.yaml"),
                python=ssh.get("python", "python3"), key=ssh.get("key"),
                log=ssh.get("log", "worker.log"),
            ))
        else:
            out.append(_HealthOnly(st_name, st["base_url"]))

    if (not wanted) or ("arm" in wanted):
        arm = raw.get("arm") or {}
        if arm.get("base_url"):
            local = arm.get("local") or {}
            port = urlparse(arm["base_url"]).port or 5004
            command = local.get("command") or [sys.executable, "-m", "arm_worker", "--port", str(port)]
            _RUN_DIR.mkdir(parents=True, exist_ok=True)
            out.append(LocalWorker(
                name="arm", base_url=arm["base_url"], command=list(command),
                log_path=_RUN_DIR / (local.get("log") or "arm_worker.log"),
                pid_path=_RUN_DIR / "arm_worker.pid",
            ))
    return out


@dataclass
class _HealthOnly:
    """A station with no ssh: block — we can only report /health, not start/stop it."""
    name: str
    base_url: str

    def is_up(self) -> bool:
        return _health(self.base_url) is not None

    def _no_ssh(self):
        raise RuntimeError(f"station '{self.name}' has no 'ssh:' block in controller.yaml — "
                           f"can't manage it remotely (start it on its host, or add an ssh: block)")

    def start(self, **_): self._no_ssh()
    def stop(self, **_): self._no_ssh()
    def logs(self, *_a, **_k): self._no_ssh()


__all__ = ["SshWorker", "LocalWorker", "workers_from_config"]
