# conscio/plugins.py
"""Third-party plugin discovery via `importlib.metadata` entry points.

A package extends Conscio by declaring entry points in its own `pyproject.toml`::

    [project.entry-points."conscio.adapters"]
    my-backend = "my_pkg:MyAdapter"        # an InferenceAdapter subclass

    [project.entry-points."conscio.sensors"]
    my-sensor  = "my_pkg:MySensor"         # a SensorAdapter subclass

    [project.entry-points."conscio.tools"]
    my-tools   = "my_pkg:register_tools"   # a callable (factory / registrar)

Discovery is **resilient**: a plugin that fails to import, or resolves to the
wrong type, is skipped with a warning — one broken third-party plugin can never
break the host. Uses the stdlib only (no new dependency).
"""
from __future__ import annotations

import importlib.metadata as _im
import warnings
from typing import Any, Callable

__all__ = [
    "load_entry_points",
    "discover_adapters",
    "discover_sensors",
    "discover_tools",
]


def load_entry_points(group: str) -> dict[str, Any]:
    """Load every entry point in `group` → {name: loaded object}.

    A plugin whose `.load()` raises is skipped with a warning, not propagated.
    """
    found: dict[str, Any] = {}
    for ep in _im.entry_points().select(group=group):
        try:
            found[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 — isolate one bad plugin
            warnings.warn(
                f"conscio: skipping plugin '{ep.name}' in group '{group}': "
                f"{exc!r}", stacklevel=2)
    return found


def _typed(group: str, predicate: Callable[[Any], bool], what: str
           ) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, obj in load_entry_points(group).items():
        try:
            ok = predicate(obj)
        except Exception:  # noqa: BLE001 — a hostile predicate target
            ok = False
        if ok:
            out[name] = obj
        else:
            warnings.warn(
                f"conscio: skipping plugin '{name}' in group '{group}': "
                f"not a {what}", stacklevel=2)
    return out


def discover_adapters() -> dict[str, Any]:
    """Discover `conscio.adapters` plugins (InferenceAdapter subclasses)."""
    from .agency.adapter import InferenceAdapter
    return _typed(
        "conscio.adapters",
        lambda o: isinstance(o, type) and issubclass(o, InferenceAdapter),
        "InferenceAdapter subclass")


def discover_sensors() -> dict[str, Any]:
    """Discover `conscio.sensors` plugins (SensorAdapter subclasses)."""
    from .perception import SensorAdapter
    return _typed(
        "conscio.sensors",
        lambda o: isinstance(o, type) and issubclass(o, SensorAdapter),
        "SensorAdapter subclass")


def discover_tools() -> dict[str, Any]:
    """Discover `conscio.tools` plugins (callable factories / registrars)."""
    return _typed("conscio.tools", callable, "callable")
