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
from .structural_consent import sync_structure

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
                 consent: Any = None,
                 pidfile: Optional[str | Path] = None,
                 heartbeat_path: Optional[str | Path] = None,
                 close_engine_on_shutdown: bool = True) -> None:
        self.engine = engine
        self.sensors = list(sensors)
        self.interval = interval
        self.budget = budget
        self.on_cycle = on_cycle or (lambda frames, result: None)
        self.workspace = workspace
        self.consent = consent                          # v1.7.2: structural consent
        self._synced_ws_id: Optional[str] = None        # re-sync only on ws change
        storage = Path(getattr(engine, "storage", "."))
        self.pidfile = Path(pidfile) if pidfile else storage / "daemon.pid"
        self.heartbeat_path = (Path(heartbeat_path) if heartbeat_path
                               else storage / "daemon_heartbeat.json")
        self.close_engine_on_shutdown = close_engine_on_shutdown
        self.cycles = 0
        self._last_report: Optional[RunReport] = None   # v1.6: last-cycle summary
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
                ws = self.workspace.poll()
                # v1.7.2: re-sync structure only when the workspace id changes
                # (STABLE syncs once; SWITCHING syncs on each switch — cheap).
                if (self.consent is not None and ws is not None
                        and ws.id != self._synced_ws_id):
                    status = sync_structure(self.engine, ws, self.consent)
                    self._synced_ws_id = ws.id
                    log.info("structure sync [%s]: %s", ws.id[:8], status)
            except Exception as exc:
                log.warning("workspace/consent sync failed: %s", exc)
        result = self.engine.run(self.budget, world_state=world_state)
        self.cycles += 1
        self._last_report = result
        # Write heartbeat every cycle (not just on shutdown)
        self._write_heartbeat()
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
        data: dict[str, Any] = {
            "ts": time.time(),
            "cycles": self.cycles,
            "awake": bool(getattr(self.engine, "awake", False)),
            "pid": os.getpid(),
        }
        # v1.6 (#5/#9): carry Conscio's output so a host can tail this file.
        if self._last_report is not None:
            r = self._last_report
            data["last_run"] = {
                "cycles": getattr(r, "cycles", 0),
                "failures": getattr(r, "failures", 0),
                "stopped": getattr(r, "stopped", ""),
            }
        advisory_fn = getattr(self.engine, "advisory", None)
        if callable(advisory_fn):
            try:
                data["advisory"] = advisory_fn()
            except Exception as exc:        # a bad advisory never breaks liveness
                log.warning("advisory snapshot failed: %s", exc)
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


# ── config loader (adapter block from ~/.config/conscio/config.json) ──────────

_CONFIG_PATHS = [
    Path.home() / ".config" / "conscio" / "config.json",
    Path.home() / ".conscio" / "config.json",
]

def _load_config() -> dict:
    """Load the first existing conscio config file. Returns {} on failure."""
    for path in _CONFIG_PATHS:
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def _build_adapter_from_config(cfg: dict, *, fallback_model: str):
    """Build an InferenceAdapter from the config's 'adapter' block.

    Returns (adapter, adapter_type_str) or (None, None) if no config adapter.
    Config adapter keys: type (required), model, api_key, base_url.
    CLI args always override config values.
    """
    adapter_cfg = cfg.get("adapter")
    if not isinstance(adapter_cfg, dict):
        return None, None
    atype = adapter_cfg.get("type")
    if not atype:
        return None, None

    from .agency.adapters import (
        AnthropicAdapter, GeminiAdapter, LMStudioAdapter,
        OllamaAdapter, OpenAIAdapter, OpenAICompatAdapter,
    )

    model = adapter_cfg.get("model") or fallback_model
    api_key = adapter_cfg.get("api_key", "")
    base_url = adapter_cfg.get("base_url")

    if atype == "lmstudio":
        return LMStudioAdapter(model=model,
                                base_url=base_url or "http://localhost:1234/v1"), atype
    if atype == "ollama":
        return OllamaAdapter(model=model,
                              base_url=base_url or "http://localhost:11434"), atype
    if atype == "openai":
        return OpenAIAdapter(model=model,
                              base_url=base_url or "https://api.openai.com/v1",
                              api_key=api_key), atype
    if atype == "anthropic":
        return AnthropicAdapter(model=model,
                                 base_url=base_url or "https://api.anthropic.com",
                                 api_key=api_key), atype
    if atype == "gemini":
        return GeminiAdapter(model=model,
                              base_url=base_url or "https://generativelanguage.googleapis.com",
                              api_key=api_key), atype
    if atype == "openai-compat":
        return OpenAICompatAdapter(model=model,
                                    base_url=base_url or "http://localhost:8000/v1",
                                    api_key=api_key), atype
    log.warning("unknown adapter type %r in config; ignoring", atype)
    return None, None


def _build_adapter_from_cli(args, fallback_model: str):
    """Build an InferenceAdapter from CLI --adapter flag."""
    from .agency.adapters import (
        AnthropicAdapter, GeminiAdapter, LMStudioAdapter,
        OllamaAdapter, OpenAIAdapter, OpenAICompatAdapter,
    )
    adapter_model = args.adapter_model or fallback_model
    if args.adapter == "lmstudio":
        return LMStudioAdapter(model=adapter_model,
                                base_url=args.base_url or "http://localhost:1234/v1")
    if args.adapter == "ollama":
        return OllamaAdapter(model=adapter_model,
                              base_url=args.base_url or "http://localhost:11434")
    if args.adapter == "openai":
        return OpenAIAdapter(model=adapter_model,
                              base_url=args.base_url or "https://api.openai.com/v1")
    if args.adapter == "anthropic":
        return AnthropicAdapter(model=adapter_model,
                                 base_url=args.base_url or "https://api.anthropic.com")
    if args.adapter == "gemini":
        return GeminiAdapter(model=adapter_model,
                              base_url=args.base_url or "https://generativelanguage.googleapis.com")
    # openai-compat
    return OpenAICompatAdapter(model=adapter_model,
                                base_url=args.base_url or "http://localhost:8000/v1")


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .engine import ConsciousnessEngine
    from .workspace import WorkspaceContext
    from .structural_consent import StructuralConsent, consent_path

    parser = argparse.ArgumentParser(
        prog="conscio-daemon",
        description="Run Conscio as a living perceive→reflect→act heartbeat.")
    parser.add_argument("--model", default=None,
                        help="model name (default: config or CONSCIO_MODEL env or glm-5.1)")
    parser.add_argument("--storage", default=None)
    parser.add_argument("--interval", type=float, default=None,
                        help="seconds between heartbeats (default: config or 5)")
    parser.add_argument("--sensors", default=None,
                        help="comma list: host,agent (default: config or host)")
    parser.add_argument("--agent-source", default=None,
                        help="peer state dir for the 'agent' sensor")
    parser.add_argument("--budget-cycles", type=int, default=None,
                        help="max act cycles per heartbeat (awake only)")
    parser.add_argument("--once", action="store_true",
                        help="run a single cycle and exit (for testing)")
    parser.add_argument("--awake", action="store_true", default=None,
                        help="wake the engine before running (R9; default OFF)")
    parser.add_argument("--adapter", default=None,
                        choices=["lmstudio", "ollama", "openai-compat",
                                 "openai", "anthropic", "gemini"],
                        help="inference adapter (overrides config)")
    parser.add_argument("--base-url", default=None,
                        help="adapter base URL (overrides config)")
    parser.add_argument("--adapter-model", default=None,
                        help="model name for the adapter (overrides config)")
    args = parser.parse_args(argv)

    # ── merge config (config < env < CLI) ──
    cfg = _load_config()
    if cfg.get("adapter"):
        log.info("loaded adapter config from file: type=%s",
                 cfg["adapter"].get("type", "?"))

    model = (args.model
             or cfg.get("model")
             or os.environ.get("CONSCIO_MODEL", "glm-5.1"))
    interval = (args.interval
                if args.interval is not None
                else cfg.get("interval", 5.0))
    sensors_spec = (args.sensors
                    or cfg.get("sensors", "host"))
    awake = args.awake if args.awake is not None else cfg.get("awake", False)

    engine = ConsciousnessEngine(model, storage_path=args.storage)

    # ── attach adapter (CLI overrides config) ──
    if args.adapter:
        adapter = _build_adapter_from_cli(args, model)
        engine.attach_adapter(adapter)
        log.info("adapter attached from CLI: %s (%s)",
                 args.adapter, args.adapter_model or model)
    else:
        adapter, atype = _build_adapter_from_config(cfg, fallback_model=model)
        if adapter is not None:
            engine.attach_adapter(adapter)
            log.info("adapter attached from config: %s (%s)",
                     atype, cfg["adapter"].get("model", model))

    if awake:
        engine.wake()
    sensors = _build_sensors(sensors_spec, agent_source=args.agent_source)
    budget = (ActBudget(max_cycles=args.budget_cycles)
              if args.budget_cycles else None)
    workspace = WorkspaceContext(emit=engine.event_bus.emit)
    consent = StructuralConsent(consent_path(engine.storage))   # v1.7.2
    daemon = Daemon(engine, sensors=sensors, interval=interval,
                    budget=budget, workspace=workspace, consent=consent)
    daemon.run(once=args.once)
    return 0


if __name__ == "__main__":            # pragma: no cover
    raise SystemExit(main())
