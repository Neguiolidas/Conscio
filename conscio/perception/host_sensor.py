# conscio/perception/host_sensor.py
"""HostSensor — the "Host Guardian" Umwelt (v1.5 "Live").

A read-only reference `SensorAdapter` that perceives host facts: load average,
disk usage, memory pressure, top processes, and (opt-in, loopback-only) local
service liveness. It is the canonical example of the v1.3 perception surface
driving the v1.5 daemon.

Design rules (spec §2/§3.B1):
- **Read-only ⇒ `Risk.LOW`.** It observes; it never mutates the host.
- **Every probe is independently guarded.** A failing probe degrades to an
  omitted line, never an exception — so a non-Linux box or a locked-down
  container yields a *reduced* frame, never a crash.
- **stdlib only**, and **no outbound network**: the optional service check is a
  *loopback* (127.0.0.1) liveness probe of the operator's own services, off by
  default; the default `perceive()` opens no socket at all.
- **`subprocess` is timeout-bounded** so a wedged `ps` can never hang the daemon.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Sequence

from .sensor import PerceptionFrame, SensorAdapter
from ..risk import Risk


def _read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into {key: kB}. Returns {} on any failure (non-Linux,
    permission, malformed) — the mem probe then simply omits its line."""
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                fields = rest.split()
                if fields:
                    try:
                        info[key.strip()] = int(fields[0])
                    except ValueError:
                        continue
    except OSError:
        return {}
    return info


class HostSensor(SensorAdapter):
    """Read-only host facts as a `PerceptionFrame` (source ``"host"``)."""

    name = "host"
    risk = Risk.LOW

    def __init__(self, *, path: str = "/", top_n: int = 5,
                 services: Sequence[int] | None = None,
                 ps_timeout: float = 2.0, connect_timeout: float = 0.2) -> None:
        self.path = path
        self.top_n = top_n
        # Loopback-only liveness: a list of LOCAL ports to check, default none.
        self.services: tuple[int, ...] = tuple(services or ())
        self.ps_timeout = ps_timeout
        self.connect_timeout = connect_timeout

    def perceive(self) -> PerceptionFrame:
        observations: list[str] = []
        signals: dict[str, float] = {}
        self._probe_load(observations, signals)
        self._probe_disk(observations, signals)
        self._probe_mem(observations, signals)
        self._probe_top(observations)
        self._probe_services(observations, signals)
        if not observations:
            observations.append("host: no probes available")
        return PerceptionFrame(source="host", observations=observations,
                               signals=signals, ts=time.time())

    # ── individually guarded probes ─────────────────────────────────────────
    def _probe_load(self, obs: list[str], sig: dict[str, float]) -> None:
        try:
            load1, load5, load15 = os.getloadavg()
        except (OSError, AttributeError):           # unavailable / non-POSIX
            return
        obs.append(f"load avg: {load1:.2f} {load5:.2f} {load15:.2f}")
        sig["load"] = round(load1, 2)

    def _probe_disk(self, obs: list[str], sig: dict[str, float]) -> None:
        try:
            usage = shutil.disk_usage(self.path)
        except (OSError, ValueError):
            return
        pct = (usage.used / usage.total * 100.0) if usage.total else 0.0
        obs.append(f"disk {self.path}: {pct:.1f}% used "
                   f"({usage.free // (1024 ** 3)} GiB free)")
        sig["disk_pct"] = round(pct, 1)

    def _probe_mem(self, obs: list[str], sig: dict[str, float]) -> None:
        info = _read_meminfo()
        total = info.get("MemTotal", 0)
        if not total:
            return
        available = info.get("MemAvailable", info.get("MemFree", 0))
        used_pct = (total - available) / total * 100.0
        obs.append(f"memory: {used_pct:.1f}% used "
                   f"({available // 1024} MiB available)")
        sig["mem_pct"] = round(used_pct, 1)

    def _probe_top(self, obs: list[str]) -> None:
        try:
            proc = subprocess.run(
                ["ps", "-eo", "comm,%cpu", "--sort=-%cpu"],
                capture_output=True, text=True,
                timeout=self.ps_timeout, check=False)
        except (OSError, ValueError, subprocess.SubprocessError):
            return
        rows = proc.stdout.strip().splitlines()[1:self.top_n + 1]
        names = [r.split(None, 1)[0] for r in rows if r.strip()]
        if names:
            obs.append("top processes: " + ", ".join(names))

    def _probe_services(self, obs: list[str], sig: dict[str, float]) -> None:
        if not self.services:
            return
        up = 0
        for port in self.services:
            alive = self._port_alive(port)
            up += int(alive)
            obs.append(f"service 127.0.0.1:{port}: "
                       f"{'up' if alive else 'down'}")
        sig["services_up"] = float(up)

    def _port_alive(self, port: int) -> bool:
        # OSError = refused/timeout; OverflowError/ValueError = bad port number
        # (raised on some platforms) — all mean "not a live local service".
        try:
            with socket.create_connection(("127.0.0.1", port),
                                          timeout=self.connect_timeout):
                return True
        except (OSError, OverflowError, ValueError):
            return False
