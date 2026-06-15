"""Daemon — the heartbeat that makes Conscio a *living* process (v1.5 "Live").

A persistent loop that each cycle:
  1. polls a configured **list** of sensors (each guarded — a failing sensor is
     logged and skipped, never kills the loop),
  2. assembles their `PerceptionFrame`s into the `world_state` string,
  3. calls `engine.run(budget, world_state=...)` — the existing L3 heartbeat,
     **awake-gated** (R9: asleep ⇒ perceive + reflect only),
  4. fires the `on_cycle(frames, result)` hook — the empty forward-seam v1.6
     uses for graph re-index,
  5. polls the `WorkspaceContext` (emits `workspace:changed` on a root change),
  6. sleeps an interruptible interval.

State already lives in SQLite/JSON via the engine, so the daemon **survives
restart** with no new persistence; on shutdown it writes a small heartbeat and
releases its pidfile. `SIGTERM`/`SIGINT` finish the current cycle, then exit.
stdlib only; the only network is the engine's existing InferenceAdapter
(R7 carve-out).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .agency.loop import ActBudget, RunReport
from .perception import PerceptionFrame, SensorAdapter

log = logging.getLogger("conscio.daemon")


class DaemonAlreadyRunning(RuntimeError):
    """Raised when a live daemon already holds the pidfile for a state dir."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                 # exists, owned by another user
        return True
    except OSError:
        return False
    return True


class Daemon:
    def __init__(self, engine: Any, sensors: Sequence[SensorAdapter], *,
                 interval: float = 5.0, budget: Optional[ActBudget] = None,
                 on_cycle: Optional[Callable[[list, RunReport], Any]] = None,
                 workspace: Any = None,
                 pidfile: Optional[str | Path] = None,
                 heartbeat_path: Optional[str | Path] = None,
                 close_engine_on_shutdown: bool = True) -> None:
        self.engine = engine
        self.sensors = list(sensors)
        self.interval = interval
        self.budget = budget
        self.on_cycle = on_cycle or (lambda frames, result: None)
        self.workspace = workspace
        storage = Path(getattr(engine, "storage", "."))
        self.pidfile = Path(pidfile) if pidfile else storage / "daemon.pid"
        self.heartbeat_path = (Path(heartbeat_path) if heartbeat_path
                               else storage / "daemon_heartbeat.json")
        self.close_engine_on_shutdown = close_engine_on_shutdown
        self.cycles = 0
        self._stop = threading.Event()
        self._orig_handlers: dict[int, Any] = {}

    # ── one cycle ─────────────────────────────────────────────────────────────
    def cycle(self) -> RunReport:
        frames: list[PerceptionFrame] = []
        for sensor in self.sensors:
            try:
                frames.append(sensor.perceive())
            except Exception as exc:        # one bad sensor never kills the loop
                log.warning("sensor %r failed: %s",
                            getattr(sensor, "name", sensor), exc)
        world_state = self.assemble(frames)
        if self.workspace is not None:
            try:
                self.workspace.poll()
            except Exception as exc:
                log.warning("workspace poll failed: %s", exc)
        result = self.engine.run(self.budget, world_state=world_state)
        self.cycles += 1
        try:
            self.on_cycle(frames, result)
        except Exception as exc:            # a misbehaving hook is non-fatal
            log.warning("on_cycle hook failed: %s", exc)
        return result

    @staticmethod
    def assemble(frames: Sequence[PerceptionFrame]) -> str:
        return "\n\n".join(f.to_world_state() for f in frames)

    # ── loop + lifecycle ──────────────────────────────────────────────────────
    @property
    def should_stop(self) -> bool:
        return self._stop.is_set()

    def run(self, once: bool = False) -> None:
        self._acquire_pidfile()
        self._install_signal_handlers()
        if self.engine.awake:
            log.warning("daemon resuming in AWAKE mode (autonomous operation ON)")
        try:
            while not self._stop.is_set():
                self.cycle()
                if once:
                    break
                self._stop.wait(timeout=self.interval)   # interruptible sleep
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self._write_heartbeat()
        if self.close_engine_on_shutdown:
            try:
                self.engine.close()
            except Exception as exc:
                log.warning("engine close failed: %s", exc)
        self._restore_signal_handlers()
        self._release_pidfile()

    # ── signals ───────────────────────────────────────────────────────────────
    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._orig_handlers[sig] = signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):   # not main thread / unsupported
                pass

    def _restore_signal_handlers(self) -> None:
        for sig, handler in self._orig_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        self._orig_handlers.clear()

    def _handle_signal(self, signum, frame) -> None:
        log.info("daemon received signal %s; finishing cycle and stopping", signum)
        self._stop.set()

    # ── pidfile (advisory, single-daemon-per-state-dir) ───────────────────────
    def _acquire_pidfile(self) -> None:
        if self.pidfile.exists():
            try:
                old = int(self.pidfile.read_text().strip())
            except (OSError, ValueError):
                old = None
            if old is not None and _pid_alive(old):
                raise DaemonAlreadyRunning(
                    f"daemon already running (pid {old}) at {self.pidfile}")
            # otherwise the holder is dead (stale pidfile) -> reclaim
        self.pidfile.parent.mkdir(parents=True, exist_ok=True)
        self.pidfile.write_text(str(os.getpid()))

    def _release_pidfile(self) -> None:
        try:
            if (self.pidfile.exists()
                    and self.pidfile.read_text().strip() == str(os.getpid())):
                self.pidfile.unlink()
        except OSError:
            pass

    def _write_heartbeat(self) -> None:
        data = {
            "ts": time.time(),
            "cycles": self.cycles,
            "awake": bool(getattr(self.engine, "awake", False)),
            "pid": os.getpid(),
        }
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            self.heartbeat_path.write_text(json.dumps(data, indent=2))
        except OSError as exc:
            log.warning("heartbeat write failed: %s", exc)


# ── entry point (conscio-daemon) ───────────────────────────────────────────────

def _build_sensors(spec: str, *, agent_source: Optional[str]) -> list[SensorAdapter]:
    from .perception import AgentSensor, HostSensor
    sensors: list[SensorAdapter] = []
    for name in (s.strip() for s in spec.split(",") if s.strip()):
        if name == "host":
            sensors.append(HostSensor())
        elif name == "agent":
            if agent_source:
                sensors.append(AgentSensor(agent_source))
            else:
                log.warning("--sensors includes 'agent' but no --agent-source; "
                            "skipping")
        else:
            log.warning("unknown sensor %r; skipping", name)
    return sensors


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .engine import ConsciousnessEngine
    from .workspace import WorkspaceContext

    parser = argparse.ArgumentParser(
        prog="conscio-daemon",
        description="Run Conscio as a living perceive→reflect→act heartbeat.")
    parser.add_argument("--model", default=os.environ.get("CONSCIO_MODEL", "glm-5.1"))
    parser.add_argument("--storage", default=None)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--sensors", default="host",
                        help="comma list: host,agent")
    parser.add_argument("--agent-source", default=None,
                        help="peer state dir for the 'agent' sensor")
    parser.add_argument("--budget-cycles", type=int, default=None,
                        help="max act cycles per heartbeat (awake only)")
    parser.add_argument("--once", action="store_true",
                        help="run a single cycle and exit (for testing)")
    parser.add_argument("--awake", action="store_true",
                        help="wake the engine before running (R9; default OFF)")
    args = parser.parse_args(argv)

    engine = ConsciousnessEngine(args.model, storage_path=args.storage)
    if args.awake:
        engine.wake()
    sensors = _build_sensors(args.sensors, agent_source=args.agent_source)
    budget = (ActBudget(max_cycles=args.budget_cycles)
              if args.budget_cycles else None)
    workspace = WorkspaceContext(emit=engine.event_bus.emit)
    daemon = Daemon(engine, sensors=sensors, interval=args.interval,
                    budget=budget, workspace=workspace)
    daemon.run(once=args.once)
    return 0


if __name__ == "__main__":            # pragma: no cover
    raise SystemExit(main())
