# conscio/perception/agent_sensor.py
"""AgentSensor — perceive another agent's session state (v1.5 "Live").

A read-only reference `SensorAdapter` that lets Conscio act as the
consciousness-layer *for* (really: *watching*) a peer agent — an OpenClaw /
Claude Code / Hermes worker backed by Conscio storage. It surfaces the peer's
**open goals**, **last reflection**, and **last handoff** as observations so the
host engine can reflect on what the peer is doing.

Design rules (spec §3.B2, plan R9):
- **Read-only ⇒ `Risk.LOW`.** It opens the peer's files for reading only and
  **never** writes them — a safety property, asserted byte-for-byte in tests.
  (It therefore reads the peer's *durable file artifacts* — the consciousness
  ``state_summary.json`` written by `ContextManager.save_state` and the handoff
  markdown written by `SessionLifecycle` — rather than opening the peer's live
  SQLite DB, whose WAL would make the no-write guarantee unprovable.)
- **Never raises.** A missing / locked / malformed source degrades to an
  "unavailable" frame; each read is individually guarded.
- **stdlib only; no network.**
"""
from __future__ import annotations

import json
from pathlib import Path

from .sensor import PerceptionFrame, SensorAdapter
from ..risk import Risk

# Handoff artifact names — mirror SessionLifecycle's HANDOFF_PATH / HEARTBEAT_PATH.
_HANDOFF_NAMES = ("_session_handoff.md", "_latest_heartbeat.md")


class AgentSensor(SensorAdapter):
    """Read a peer agent's state from its Conscio storage dir (read-only)."""

    name = "agent"
    risk = Risk.LOW

    def __init__(self, source: str | Path, *, name: str | None = None,
                 max_chars: int = 2000, max_goals: int = 5) -> None:
        self.source = Path(source)
        self.peer_name = name or self.source.name or "peer"
        self.max_chars = max_chars
        self.max_goals = max_goals

    def perceive(self) -> PerceptionFrame:
        observations: list[str] = []
        signals: dict[str, float] = {}
        if not self.source.exists():
            return PerceptionFrame(
                source="agent",
                observations=[f"agent '{self.peer_name}': "
                              f"source unavailable ({self.source})"])
        observations.append(f"peer: {self.peer_name}")
        self._read_state(observations, signals)
        self._read_handoff(observations)
        return PerceptionFrame(source="agent", observations=observations,
                               signals=signals)

    # ── guarded reads ───────────────────────────────────────────────────────
    def _read_state(self, obs: list[str], sig: dict[str, float]) -> None:
        path = self.source / "state_summary.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):               # absent / locked / malformed
            return
        goals = data.get("active_goals") or []
        if goals:
            shown = "; ".join(str(g) for g in goals[:self.max_goals])
            obs.append(f"open goals: {shown}")
            sig["open_goals"] = float(len(goals))
        reflection = data.get("last_reflection")
        if reflection:
            obs.append(f"last reflection: {reflection}")
        if data.get("awake") is not None:
            obs.append(f"peer awake: {bool(data['awake'])}")

    def _read_handoff(self, obs: list[str]) -> None:
        for candidate in self._handoff_candidates():
            try:
                text = candidate.read_text()
            except OSError:
                continue
            text = text.strip()
            if text:
                obs.append(f"last handoff ({candidate.name}): "
                           f"{text[:self.max_chars]}")
                return

    def _handoff_candidates(self) -> list[Path]:
        found: list[Path] = []
        for nm in _HANDOFF_NAMES:
            p = self.source / nm
            if p.exists():
                found.append(p)
        # also tolerate a nested handoff/ dir or arbitrarily-named handoffs
        try:
            for pat in ("*handoff*.md", "*heartbeat*.md"):
                for p in sorted(self.source.glob(pat)):
                    if p not in found:
                        found.append(p)
        except OSError:
            pass
        return found
