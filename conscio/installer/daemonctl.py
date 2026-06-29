"""Awake-daemon lifecycle for a per-host space. PID-file + cmdline-verified
liveness (never kill a recycled PID, R4) + start_new_session so the daemon
survives the launching shell's logout (R4).

Container note: /proc may be absent (no /proc mounted). When _cmdline() returns
"" we fall back to liveness-only (os.kill(pid, 0)) — documented, accepted."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from . import spaces

_MARKERS = ("conscio-daemon", "conscio daemon", "conscio.daemon")


def pid_file(slug: str) -> Path:
    return spaces.DAEMONS_ROOT() / f"{slug}.pid"


def _read_pid(slug: str) -> "int | None":
    try:
        return int(pid_file(slug).read_text().strip())
    except (OSError, ValueError):
        return None


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_running(slug: str) -> bool:
    pid = _read_pid(slug)
    if pid is None or not _alive(pid):
        return False
    cmd = _cmdline(pid)
    if not cmd:                       # no /proc (container/non-Linux): liveness only
        return True
    return any(m in cmd for m in _MARKERS)


def start(slug: str, *, extra_args: "list[str]") -> int:
    if is_running(slug):
        return _read_pid(slug) or -1
    pf = pid_file(slug)
    pf.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["conscio", "daemon", "--storage", str(spaces.space_dir(slug)),
         *extra_args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)              # R4: survive SIGHUP on logout
    pf.write_text(str(proc.pid))
    return proc.pid


def stop(slug: str) -> bool:
    if not is_running(slug):
        pid_file(slug).unlink(missing_ok=True)
        return False
    pid = _read_pid(slug)
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    pid_file(slug).unlink(missing_ok=True)
    return True
