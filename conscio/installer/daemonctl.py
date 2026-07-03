"""Awake-daemon lifecycle for a per-host space. PID-file + cmdline-verified
liveness (never kill a recycled PID, R4) + start_new_session so the daemon
survives the launching shell's logout (R4).

Container note: /proc may be absent (no /proc mounted). When _cmdline() returns
"" we fall back to liveness-only (os.kill(pid, 0)) — documented, accepted."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from . import spaces

_MARKERS = ("conscio-daemon", "conscio daemon", "conscio.daemon")
# Instant-crash window checked before trusting the pid. Best-effort: 0.5s
# covers interpreter startup + argparse rejection on typical machines; a
# slower crash still surfaces on the next is_running() (cmdline markers).
_SPAWN_GRACE_S = 0.5


class DaemonStartError(RuntimeError):
    pass


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
    env = dict(os.environ)
    # Same per-host binding the MCP launch entry gets (hostcfg.mcp_server_entry)
    # — daemon and server of one space must resolve the SAME key vault.
    env["CONSCIO_VAULT_DIR"] = str(spaces.vault_dir(slug))
    try:
        proc = subprocess.Popen(
            ["conscio", "daemon", "--storage", str(spaces.space_dir(slug)),
             *extra_args],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, env=env)  # R4: survive SIGHUP on logout
    except OSError as exc:                    # e.g. conscio not on PATH
        raise DaemonStartError(f"cannot launch conscio daemon: {exc}") from exc
    time.sleep(_SPAWN_GRACE_S)
    if proc.poll() is not None:
        raise DaemonStartError(
            f"daemon exited immediately (code {proc.returncode}); "
            f"args: {extra_args}")
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
        except PermissionError:
            # A marker-matched conscio daemon we cannot signal = another
            # user's daemon on a shared base. Keeping the pidfile refuses to
            # double-spawn onto its storage; the escape hatch is deleting the
            # pidfile by hand. (A recycled PID without markers never gets
            # here — is_running() already unlinked above.)
            return False                     # not ours to kill; keep the pidfile
        except OSError:
            pass                             # already gone
    pid_file(slug).unlink(missing_ok=True)
    return True
