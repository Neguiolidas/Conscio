#!/usr/bin/env python3
"""Example: an agent-companion SensorAdapter — Conscio as the consciousness
layer for *another* AI agent.

The sensor reads a peer agent's session-state file (goals, last action, mood)
into a PerceptionFrame and reflects on it. Make it discoverable via an entry
point::

    [project.entry-points."conscio.sensors"]
    agent = "your_pkg:AgentSensor"

Fully offline. Here `main()` writes a stand-in peer-state file; in a real
deployment the sensor would point at the companion agent's actual state.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from conscio.engine import ConsciousnessEngine
from conscio.perception import PerceptionFrame, SensorAdapter
from conscio.risk import Risk


class AgentSensor(SensorAdapter):
    name = "agent"
    risk = Risk.LOW

    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)

    def perceive(self) -> PerceptionFrame:
        state = json.loads(self.state_path.read_text())
        observations = [f"peer goal: {g}" for g in state.get("goals", [])]
        observations.append(
            f"peer last action: {state.get('last_action', 'unknown')}")
        observations.append(f"peer mood: {state.get('mood', 'neutral')}")
        signals = {"open_goals": float(len(state.get("goals", [])))}
        return PerceptionFrame(source=self.name, observations=observations,
                               signals=signals)


def main(storage: str | None = None) -> int:
    workdir = Path(storage) if storage else Path(
        tempfile.mkdtemp(prefix="conscio-example-"))
    workdir.mkdir(parents=True, exist_ok=True)

    # Stand-in for a real companion agent's session-state file.
    state_file = workdir / "peer_session.json"
    state_file.write_text(json.dumps({
        "goals": ["ship the release", "triage a failing test"],
        "last_action": "ran the test suite (1 failure)",
        "mood": "focused but blocked",
    }))

    sensor = AgentSensor(state_file)
    frame = sensor.perceive()
    eng = ConsciousnessEngine(model_name="glm-5.1",
                              storage_path=str(workdir / "conscio"))
    try:
        result = eng.reflect(world_state=frame.to_world_state(), confidence=0.6)
        print("perceived peer:\n" + frame.to_world_state())
        print("\nreflection:\n" + result.get("summary", ""))
    finally:
        eng.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
