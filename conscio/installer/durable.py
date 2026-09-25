"""Durable host-bound space resolution (Conscio v4.8 S1).

Provides pure space resolution according to host environment and storage binding,
supporting seamless plugin survival across uninstalls, downgrades, and reinstalls.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..mcp.host_identity import derive_host_identity
from .binding import unexpanded_variable
from .spaces import slugify, space_dir

_SUPPORTED_PLUGIN_ROOT_KEYS = ("CLAUDE_PLUGIN_DATA", "ZCODE_PLUGIN_DATA")


@dataclass(frozen=True)
class SpaceResolution:
    """Result of pure space resolution."""

    kind: str
    target: Path | None
    reason: str
    repair_pointer: bool = False
    announcement: str = ""


def plugin_data_roots(env: Mapping[str, str] | None = None) -> list[Path]:
    """Return recognized plugin data roots present in the environment."""
    if env is None:
        env = os.environ
    roots: list[Path] = []
    for key in _SUPPORTED_PLUGIN_ROOT_KEYS:
        val = env.get(key)
        if val:
            roots.append(Path(val).expanduser())
    return roots


def _find_plugin_instance_json(storage: Path, root: Path | None = None) -> Path | None:
    """Locate an existing instance.json within the plugin space without side-effects."""
    candidates = [
        storage / "instance.json",
        storage / "space" / "instance.json",
    ]
    if root is not None:
        candidates.extend([
            root / "instance.json",
            root / "space" / "instance.json",
        ])
    for c in candidates:
        if c.exists():
            return c
    return None


def resolve_space(storage: Path | str, env: Mapping[str, str] | None = None) -> SpaceResolution:
    """Resolve space purely without filesystem side-effects.

    Precedence:
    lock -> pointer (B1/B5) -> B0 legacy -> durable (B2/B3) -> B4 evidence -> B6 fresh.
    """
    storage_str = str(storage)
    var = unexpanded_variable(storage_str)
    if var is not None:
        return SpaceResolution(
            kind="unexpanded",
            target=None,
            reason=f"storage path contains unexpanded variable: ${var}",
        )

    storage_path = Path(storage).expanduser()
    roots = plugin_data_roots(env)

    # Check if storage is plugin-bound
    matched_root: Path | None = None
    for r in roots:
        try:
            if storage_path == r or storage_path.is_relative_to(r):
                matched_root = r
                break
        except (ValueError, OSError):
            continue

    if matched_root is None:
        # Explicit storage outside plugin data roots retains legacy behaviour unchanged
        return SpaceResolution(
            kind="explicit",
            target=storage_path,
            reason="",
        )

    # Determine runtime and slug
    host_ident = derive_host_identity(env)
    runtime = host_ident.runtime or "default"
    slug = slugify(runtime)
    durable_target = space_dir(slug)

    # Check existence of instance.json in plugin vs durable
    plugin_inst_path = _find_plugin_instance_json(storage_path, matched_root)
    plugin_has_instance = plugin_inst_path is not None
    durable_inst_path = durable_target / "instance.json"
    durable_has_instance = durable_inst_path.exists()

    if plugin_has_instance and not durable_has_instance:
        # B0: Legacy plugin space in use until migrated
        return SpaceResolution(
            kind="B0",
            target=storage_path,
            reason="",
            repair_pointer=False,
            announcement="migration ready, run: conscio space migrate",
        )

    if not plugin_has_instance and durable_has_instance:
        # B2: Adopt durable space (e.g. after plugin wipe)
        announcement = ""
        tombstone = durable_target / "migrated-from.json"
        if tombstone.exists():
            try:
                data = json.loads(durable_inst_path.read_text(encoding="utf-8"))
                iid = str(data.get("instance_id", ""))
                id8 = iid[:8]
            except Exception:
                id8 = "unknown"
            announcement = (
                f"space adopted for {slug} (instance {id8}); reactor unit for this "
                "agent points to legacy path — re-arm with: conscio relay service ..."
            )
        return SpaceResolution(
            kind="B2",
            target=durable_target,
            reason="",
            repair_pointer=True,
            announcement=announcement,
        )

    if not plugin_has_instance and not durable_has_instance:
        # B6: Fresh mint on durable space
        return SpaceResolution(
            kind="B6",
            target=durable_target,
            reason="",
            repair_pointer=True,
            announcement="",
        )

    # Fallback placeholder (B3/B4 will be handled in subsequent tasks)
    return SpaceResolution(
        kind="unhandled",
        target=None,
        reason=f"unhandled state for slug {slug}",
    )
