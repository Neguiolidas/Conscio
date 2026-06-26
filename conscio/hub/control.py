"""Daemon control-file IO (the WRITE half of v2.8.1 "Reins").

The Hub writes the operator's awake intent here; a daemon launched with
`--watch-control` reads it at the top of each cycle and applies it via
`engine.wake()`/`engine.sleep()`. Pure, engine-free, atomic. The filename is a
contract shared by name only — the daemon reads it with `safe_read_json` and
never imports this module (layering).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..guards import safe_read_json

CONTROL_FILENAME = "daemon_control.json"


def write_control(storage: Path, awake: bool) -> dict:
    storage = Path(storage)
    data = {"awake": bool(awake), "ts": time.time()}
    path = storage / CONTROL_FILENAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)                       # atomic: no torn read
    return data


def read_control(storage: Path) -> dict:
    return safe_read_json(Path(storage) / CONTROL_FILENAME) or {}
