#!/usr/bin/env python3
"""Example: a host-guardian SensorAdapter feeding engine.reflect().

A read-only sensor reads host facts (disk, CPUs, load) into a PerceptionFrame;
`frame.to_world_state()` becomes the world_state that reflect() already accepts —
so reflect() is never touched. Make it discoverable via an entry point::

    [project.entry-points."conscio.sensors"]
    host = "your_pkg:HostSensor"

Fully offline. The reference production HostSensor lands in a later release; this
shows the stable interface you can build against today.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from conscio.engine import ConsciousnessEngine
from conscio.perception import PerceptionFrame, SensorAdapter
from conscio.risk import Risk


class HostSensor(SensorAdapter):
    name = "host"
    risk = Risk.LOW                       # read-only observation

    def perceive(self) -> PerceptionFrame:
        total, used, free = shutil.disk_usage("/")
        pct = used / total * 100
        observations = [
            f"disk: {pct:.0f}% used ({free // (1 << 30)} GiB free)",
            f"cpus: {os.cpu_count()}",
        ]
        signals = {"disk_used_pct": round(pct, 1)}
        try:
            load1, load5, load15 = os.getloadavg()
            observations.append(
                f"loadavg: {load1:.2f} {load5:.2f} {load15:.2f}")
            signals["load1"] = round(load1, 2)
        except (OSError, AttributeError):
            pass                          # not available on every platform
        return PerceptionFrame(source=self.name, observations=observations,
                               signals=signals)


def main(storage: str | None = None) -> int:
    storage = storage or tempfile.mkdtemp(prefix="conscio-example-")
    sensor = HostSensor()
    frame = sensor.perceive()
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=storage)
    try:
        result = eng.reflect(world_state=frame.to_world_state(), confidence=0.7)
        print("perceived:\n" + frame.to_world_state())
        print("\nreflection:\n" + result.get("summary", ""))
    finally:
        eng.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
