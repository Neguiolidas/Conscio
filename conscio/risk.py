# conscio/risk.py
"""The Risk vocabulary — a single safety-tier enum shared across subsystems.

Both the action surface (`conscio.agency.tools.ToolRegistry`) and the perception
surface (`conscio.perception.SensorAdapter`) classify operations by risk. They
share *one* vocabulary so a host reasons about action risk and perception risk in
the same terms. `conscio.agency.tools` re-exports `Risk` for backward
compatibility — every historical import path (`from conscio.agency.tools import
Risk`, `from conscio.agency import Risk`) keeps resolving to this exact object.
"""
from __future__ import annotations

from enum import Enum


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
