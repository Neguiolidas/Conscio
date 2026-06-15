# conscio/perception/__init__.py
"""Perception — the pluggable sensor surface (mirror of the inference surface).

Public, stable extension point as of v1.3. Write a `SensorAdapter`, produce a
`PerceptionFrame`, and feed `frame.to_world_state()` into `engine.reflect()`.
"""
from .agent_sensor import AgentSensor
from .host_sensor import HostSensor
from .sensor import MockSensor, PerceptionFrame, SensorAdapter

__all__ = ["SensorAdapter", "PerceptionFrame", "MockSensor", "HostSensor",
           "AgentSensor"]
