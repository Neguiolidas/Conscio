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
from .guards import safe_read_json
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
                 responder: Optional[Callable[[], list]] = None,
                 initiator: Optional[Callable[[], list]] = None,
                 initiate_interval: float = 300.0,
                 workspace: Any = None,
                 consent: Any = None,
                 pidfile: Optional[str | Path] = None,
                 heartbeat_path: Optional[str | Path] = None,
                 control_path: Optional[str | Path] = None,
                 close_engine_on_shutdown: bool = True) -> None:
        self.engine = engine
        self.sensors = list(sensors)
        self.interval = interval
        self.budget = budget
        self.on_cycle = on_cycle or (lambda frames, result: None)
        self.responder = responder                      # v2.7.0: relay auto-reply
        self.initiator = initiator                      # v2.10.0: proactive init
        self.initiate_interval = float(initiate_interval)
        self._last_initiate: Optional[float] = None       # None = never initiated
        self.workspace = workspace
        self.consent = consent                          # v1.7.2: structural consent
        self._synced_ws_id: Optional[str] = None        # re-sync only on ws change
        storage = Path(getattr(engine, "storage", "."))
        self.pidfile = Path(pidfile) if pidfile else storage / "daemon.pid"
        self.heartbeat_path = (Path(heartbeat_path) if heartbeat_path
                               else storage / "daemon_heartbeat.json")
        self.control_path = Path(control_path) if control_path else None
        self.close_engine_on_shutdown = close_engine_on_shutdown
        self.cycles = 0
        self._last_report: Optional[RunReport] = None   # v1.6: last-cycle summary
        self._stop = threading.Event()
        self._orig_handlers: dict[int, Any] = {}

    # ── one cycle ─────────────────────────────────────────────────────────────
    def cycle(self) -> RunReport:
        # v2.8.1: honor the Hub's awake control file (off unless --watch-control).
        # File contract only — never imports conscio.hub. wake()/sleep() because
        # engine.awake is a read-only property.
        if self.control_path is not None:
            ctrl = safe_read_json(self.control_path) or {}
            desired = ctrl.get("awake")
            if isinstance(desired, bool) and desired != self.engine.awake:
                self.engine.wake() if desired else self.engine.sleep()
                log.warning("daemon awake -> %s (control file)", desired)
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
        if self.responder is not None and self.engine.awake:   # v2.10.0: runtime
            try:
                self.responder()
            except Exception as exc:        # a bad responder never kills the loop
                log.warning("relay responder failed: %s", exc)
        if self.initiator is not None and self.engine.awake:   # v2.10.0: proactive
            now = time.monotonic()
            if (self._last_initiate is None                 # first cycle: fire
                    or now - self._last_initiate >= self.initiate_interval):
                self._last_initiate = now               # consume window pre-call
                try:
                    self.initiator()
                except Exception as exc:    # a bad initiator never kills the loop
                    log.warning("relay initiator failed: %s", exc)
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
            # B-012: atomic write so a host tailing the heartbeat (v1.6 #5/#9)
            # never reads a torn/partial file — write a sibling tmp then replace.
            tmp = self.heartbeat_path.with_name(self.heartbeat_path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, self.heartbeat_path)
        except OSError as exc:
            log.warning("heartbeat write failed: %s", exc)


# ── entry point (conscio-daemon) ───────────────────────────────────────────────

def _build_sensors(spec: str, *, agent_source: Optional[str],
                   liaison_db=None, self_id: str = "",
                   relay_peers: Sequence[str] = ()) -> list[SensorAdapter]:
    from .perception import AgentSensor, HostSensor
    from .perception.relay_sensor import RelaySensor
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
        elif name == "relay":
            if self_id:
                sensors.append(RelaySensor(liaison_db, self_id,
                                           tuple(relay_peers)))
            else:
                log.warning("--sensors includes 'relay' but no instance "
                            "identity; skipping")
        else:
            log.warning("unknown sensor %r; skipping", name)
    return sensors


def _responder_armed(*, auto_respond: bool, relay_peer, has_adapter: bool,
                     awake: bool, sensors_spec: str) -> bool:
    """True iff --auto-respond should arm: needs a relay sensor + an adapter +
    --awake + at least one --relay-peer (v2.7.0)."""
    return bool(auto_respond and relay_peer and has_adapter and awake
                and "relay" in [s.strip() for s in sensors_spec.split(",")])


def _initiator_armed(*, initiate: bool, relay_peer, has_adapter: bool,
                     awake: bool, sensors_spec: str) -> bool:
    """True iff --initiate should arm: needs a relay sensor + an adapter +
    --awake + at least one --relay-peer (v2.10.0). This is the startup arm gate;
    runtime awake is re-checked each cycle in Daemon.cycle()."""
    return bool(initiate and relay_peer and has_adapter and awake
                and "relay" in [s.strip() for s in sensors_spec.split(",")])


# ── config loader + adapter builder live in conscio/adapter_config.py now
# (shared by the daemon and the MCP server; v2.0.1, no behavior change) ──


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


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conscio-daemon",
        description="Run Conscio as a living perceive→reflect→act heartbeat.")
    parser.add_argument("--model", default=None,
                        help="model name (default: config or CONSCIO_MODEL env or glm-5.1)")
    parser.add_argument("--storage", default=None)
    parser.add_argument("--interval", type=float, default=None,
                        help="seconds between heartbeats (default: config or 5)")
    parser.add_argument("--sensors", default=None,
                        help="comma list: host,agent,relay (default: config or host)")
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
    parser.add_argument("--liaison-db", default=None,
                        help="mailbox db for the relay sensor "
                             "(default $HERMES_HOME/liaison.db)")
    parser.add_argument("--relay-peer", action="append", default=[],
                        metavar="INSTANCE_ID",
                        help="trusted relay peer for the relay sensor (repeatable)")
    parser.add_argument("--auto-respond", action="store_true",
                        help="daemon auto-replies to unread relay peer messages "
                             "via the adapter (OFF default; needs relay sensor + "
                             "adapter + --awake + --relay-peer)")
    parser.add_argument("--respond-limit", type=int, default=10,
                        help="max relay auto-replies per cycle (token-burn cap)")
    parser.add_argument("--cognize", action="store_true",
                        help="route relay auto-replies through engine cognition "
                             "(identity+memory+advisory, read-only) instead of a "
                             "thin adapter call; rides on --auto-respond (v2.9.0)")
    parser.add_argument("--cognize-remember", action="store_true",
                        help="when cognize-responding, also WRITE the exchange "
                             "to episodic memory (content_store; recall-able "
                             "later). Rides on --cognize; OFF default (v2.9.1)")
    parser.add_argument("--initiate", action="store_true",
                        help="awake daemon proactively OPENS directed relay "
                             "conversations with peers via engine cognition "
                             "(read-only); OFF default, needs relay sensor + "
                             "adapter + --awake + --relay-peer (v2.10.0)")
    parser.add_argument("--initiate-broadcast", action="store_true",
                        help="additionally BROADCAST proactive announcements to "
                             "all peers; requires --initiate (v2.10.0)")
    parser.add_argument("--initiate-interval", type=float, default=300.0,
                        help="min seconds between proactive initiation cycles "
                             "(cadence cap; default 300)")
    parser.add_argument("--watch-control", action="store_true",
                        help="honor daemon_control.json in the storage dir "
                             "(the Hub awake toggle); OFF default. Awake makes "
                             "an act-capable daemon autonomous.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .engine import ConsciousnessEngine
    from .workspace import WorkspaceContext
    from .structural_consent import StructuralConsent, consent_path

    args = _arg_parser().parse_args(argv)
    from .installer.binding import validate_binding          # R6
    validate_binding(args.storage)

    # ── merge config (config < env < CLI) ──
    from .adapter_config import build_adapter_from_config, load_config
    cfg = load_config()
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
        adapter, atype = build_adapter_from_config(cfg, fallback_model=model)
        if adapter is not None:
            engine.attach_adapter(adapter)
            log.info("adapter attached from config: %s (%s)",
                     atype, cfg["adapter"].get("model", model))

    if awake:
        engine.wake()
    self_id = ""
    liaison_db = None
    if "relay" in [s.strip() for s in sensors_spec.split(",")]:
        from .noosphere.identity import load_or_create
        from .liaison import mailbox
        self_id = load_or_create(engine.storage).instance_id
        liaison_db = args.liaison_db or mailbox.default_db()
    sensors = _build_sensors(sensors_spec, agent_source=args.agent_source,
                             liaison_db=liaison_db, self_id=self_id,
                             relay_peers=tuple(args.relay_peer))
    budget = (ActBudget(max_cycles=args.budget_cycles)
              if args.budget_cycles else None)
    workspace = WorkspaceContext(emit=engine.event_bus.emit)
    consent = StructuralConsent(consent_path(engine.storage))   # v1.7.2
    responder = None                                            # v2.7.0
    if args.auto_respond:
        if _responder_armed(auto_respond=True, relay_peer=args.relay_peer,
                            has_adapter=adapter is not None, awake=awake,
                            sensors_spec=sensors_spec):
            _adapter = adapter                          # R1: bind LOCAL adapter
            _peers = tuple(args.relay_peer)
            if args.cognize:
                from .agency import relay_cognize
                _engine = engine                        # R1: bind LOCAL engine
                _remember = args.cognize_remember       # v2.9.1
                responder = lambda: relay_cognize.cognize_respond(  # noqa: E731
                    _engine, _adapter, liaison_db, self_id, _peers,
                    limit=args.respond_limit, remember=_remember)
                log.info("relay cognize-respond armed (peers=%d, limit=%d, "
                         "remember=%s)", len(_peers), args.respond_limit,
                         _remember)
            else:
                from .agency import relay_respond
                responder = lambda: relay_respond.auto_respond(   # noqa: E731
                    _adapter, liaison_db, self_id, _peers,
                    limit=args.respond_limit)
                log.info("relay auto-respond armed (peers=%d, limit=%d)",
                         len(_peers), args.respond_limit)
        else:
            log.warning("--auto-respond inert: needs relay sensor + adapter + "
                        "--awake + --relay-peer; skipping")
    elif args.cognize:
        log.warning("--cognize inert without --auto-respond; skipping")
    initiator = None                                            # v2.10.0
    if args.initiate:
        if _initiator_armed(initiate=True, relay_peer=args.relay_peer,
                            has_adapter=adapter is not None, awake=awake,
                            sensors_spec=sensors_spec):
            from .agency import relay_initiate
            _i_engine = engine                          # R1: bind LOCAL engine
            _i_adapter = adapter                         # R1: bind LOCAL adapter
            _i_peers = tuple(args.relay_peer)
            _i_broadcast = args.initiate_broadcast

            def _initiator() -> list:
                out = relay_initiate.initiate(_i_engine, _i_adapter, liaison_db,
                                              self_id, _i_peers)
                if _i_broadcast:
                    out += relay_initiate.initiate(_i_engine, _i_adapter,
                                                   liaison_db, self_id, _i_peers,
                                                   broadcast=True)
                return out

            initiator = _initiator
            log.info("relay initiator armed (peers=%d, broadcast=%s, "
                     "interval=%ss)", len(_i_peers), _i_broadcast,
                     args.initiate_interval)
        else:
            log.warning("--initiate inert: needs relay sensor + adapter + "
                        "--awake + --relay-peer; skipping")
    control_path = (engine.storage / "daemon_control.json"
                    if args.watch_control else None)
    daemon = Daemon(engine, sensors=sensors, interval=interval,
                    budget=budget, workspace=workspace, consent=consent,
                    responder=responder, initiator=initiator,
                    initiate_interval=args.initiate_interval,
                    control_path=control_path)
    daemon.run(once=args.once)
    return 0


if __name__ == "__main__":            # pragma: no cover
    raise SystemExit(main())
