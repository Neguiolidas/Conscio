"""Durable host-bound space resolution (Conscio v4.8 S1).

Provides pure space resolution according to host environment and storage binding,
supporting seamless plugin survival across uninstalls, downgrades, and reinstalls.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..mcp.host_identity import derive_host_identity
from .binding import unexpanded_variable
from .spaces import minting_lock, slugify, space_dir

__all__ = [
    "SpaceResolution",
    "migration_lock_path",
    "minting_lock",
    "plugin_data_dir",
    "plugin_data_roots",
    "plugin_pointer_path",
    "plugin_refused_marker_path",
    "remove_migration_lock",
    "remove_refused_marker",
    "resolve_space",
    "write_migration_lock",
    "write_pointer_atomic",
    "write_refused_marker",
]

_SUPPORTED_PLUGIN_ROOT_KEYS = ("CLAUDE_PLUGIN_DATA", "ZCODE_PLUGIN_DATA")


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        written = 0
        while written < len(blob):
            written += os.write(fd, blob[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))


def write_pointer_atomic(
    pointer_path: Path,
    target: Path | str,
    runtime: str,
    slug: str,
    migrated_ts: float | None = None,
) -> None:
    """Atomically write space-pointer.json schema 1."""
    if migrated_ts is None:
        migrated_ts = time.time()
    payload = {
        "schema": 1,
        "target": str(target),
        "runtime": runtime,
        "slug": slug,
        "migrated_ts": float(migrated_ts),
    }
    _write_json_atomic(pointer_path, payload)


def migration_lock_path(slug: str) -> Path:
    """Return Path to ~/.conscio/instances/.migrating-<slug>."""
    return space_dir(slug).parent / f".migrating-{slug}"


def write_migration_lock(
    slug: str,
    pid: int | None = None,
    started_ts: float | None = None,
) -> Path:
    """Atomically create migration lock .migrating-<slug> schema 1."""
    if pid is None:
        pid = os.getpid()
    if started_ts is None:
        started_ts = time.time()
    payload = {
        "schema": 1,
        "pid": pid,
        "started_ts": float(started_ts),
    }
    lock_path = migration_lock_path(slug)
    _write_json_atomic(lock_path, payload)
    return lock_path


def remove_migration_lock(slug: str) -> None:
    """Remove migration lock .migrating-<slug> if present."""
    lock_path = migration_lock_path(slug)
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


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


def plugin_data_dir(storage: Path | str, env: Mapping[str, str] | None = None) -> Path:
    """Return the base plugin data directory for storage, or storage itself."""
    storage_path = Path(storage).expanduser()
    roots = plugin_data_roots(env)
    for r in roots:
        try:
            if storage_path == r or storage_path.is_relative_to(r):
                return r
        except (ValueError, OSError):
            continue
    if storage_path.name == "space":
        return storage_path.parent
    return storage_path


def plugin_pointer_path(storage: Path | str, env: Mapping[str, str] | None = None) -> Path:
    """Return the space-pointer.json path for storage."""
    return plugin_data_dir(storage, env) / "space-pointer.json"


def plugin_refused_marker_path(storage: Path | str, env: Mapping[str, str] | None = None) -> Path:
    """Return the space-refused.json path for storage."""
    return plugin_data_dir(storage, env) / "space-refused.json"


def write_refused_marker(
    storage: Path | str,
    state: str,
    reason: str,
    env: Mapping[str, str] | None = None,
    ts: float | None = None,
) -> Path:
    """Atomically write space-refused.json schema 1."""
    marker_path = plugin_refused_marker_path(storage, env)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    if ts is None:
        ts = time.time()
    payload = {
        "schema": 1,
        "state": state,
        "reason": reason,
        "ts": float(ts),
    }
    _write_json_atomic(marker_path, payload)
    return marker_path


def remove_refused_marker(storage: Path | str, env: Mapping[str, str] | None = None) -> None:
    """Remove space-refused.json if present."""
    marker_path = plugin_refused_marker_path(storage, env)
    try:
        marker_path.unlink()
    except FileNotFoundError:
        pass
    storage_path = Path(storage).expanduser()
    if storage_path != marker_path.parent:
        try:
            (storage_path / "space-refused.json").unlink()
        except FileNotFoundError:
            pass


def _find_pointer_file(storage: Path, root: Path | None = None) -> Path | None:
    """Locate space-pointer.json if present in the plugin space."""
    candidates = [
        storage / "space-pointer.json",
    ]
    if storage.parent != storage:
        candidates.append(storage.parent / "space-pointer.json")
    if root is not None:
        candidates.extend([
            root / "space-pointer.json",
            root / "space" / "space-pointer.json",
        ])
    for c in candidates:
        if c.exists():
            return c
    return None


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

    # Precedence Step 0: Check migration lock (.migrating-<slug>)
    migrating_lock_file = migration_lock_path(slug)
    if migrating_lock_file.exists():
        pid = "unknown"
        try:
            lock_data = json.loads(migrating_lock_file.read_text(encoding="utf-8"))
            pid = str(lock_data.get("pid", "unknown"))
        except Exception:
            pass
        return SpaceResolution(
            kind="lock",
            target=None,
            reason=(
                f"space migration in progress for {slug} (pid {pid}). "
                "Refusing to start: neither minting nor using the legacy path. "
                "Re-run when the migration finishes."
            ),
            repair_pointer=False,
            announcement="",
        )

    # Precedence Step 2: Check pointer (B1 / B5)
    pointer_file = _find_pointer_file(storage_path, matched_root)
    if pointer_file is not None:
        try:
            data = json.loads(pointer_file.read_text(encoding="utf-8"))
            target_str = str(data.get("target", ""))
            target_path = Path(target_str).expanduser()
            if target_path.exists():
                return SpaceResolution(
                    kind="B1",
                    target=target_path,
                    reason="",
                    repair_pointer=False,
                    announcement="",
                )
            return SpaceResolution(
                kind="B5",
                target=None,
                reason=(
                    f"space pointer found but its target {target_path} is gone. "
                    "Not minting over it — run 'conscio space migrate' to resolve, "
                    "or restore the target."
                ),
                repair_pointer=False,
                announcement="",
            )
        except Exception as exc:
            return SpaceResolution(
                kind="B5",
                target=None,
                reason=f"corrupt space pointer at {pointer_file}: {exc}",
                repair_pointer=False,
                announcement="",
            )

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

    if plugin_has_instance and durable_has_instance:
        # B3: Two space copies found
        plugin_id = ""
        durable_id = ""
        try:
            if plugin_inst_path is not None:
                p_data = json.loads(plugin_inst_path.read_text(encoding="utf-8"))
                plugin_id = str(p_data.get("instance_id", ""))
        except Exception:
            pass
        try:
            d_data = json.loads(durable_inst_path.read_text(encoding="utf-8"))
            durable_id = str(d_data.get("instance_id", ""))
        except Exception:
            pass

        if plugin_id and durable_id and plugin_id == durable_id:
            reason = (
                f"two copies of the same identity for {slug}; "
                "they may have diverged (interrupted cross-fs copy or manual copy)"
            )
        else:
            p_id8 = plugin_id[:8] if plugin_id else "unknown"
            d_id8 = durable_id[:8] if durable_id else "unknown"
            reason = (
                f"identity conflict for {slug}: plugin copy (id {p_id8}) and durable copy (id {d_id8}) "
                "belong to different identities"
            )
        return SpaceResolution(
            kind="B3",
            target=None,
            reason=reason,
            repair_pointer=False,
            announcement="",
        )

    if not plugin_has_instance and not durable_has_instance:
        # B4: Evidence of previous identity without live space
        # Check in strict order: 1. Durable tombstone, 2. Directory peer card
        tombstone = durable_target / "migrated-from.json"
        if tombstone.exists():
            return SpaceResolution(
                kind="B4",
                target=None,
                reason=(
                    f"evidence of a previous identity for {slug} was found (tombstone at {tombstone}), "
                    "but no live space exists. Not minting silently — resolve with 'conscio space migrate' "
                    "or delete the evidence explicitly."
                ),
                repair_pointer=False,
                announcement="",
            )

        try:
            from ..liaison import directory
            for card in directory.peers():
                if card.get("runtime") == runtime or card.get("slug") == slug:
                    cid = card.get("instance_id", "unknown")
                    return SpaceResolution(
                        kind="B4",
                        target=None,
                        reason=(
                            f"evidence of a previous identity for {slug} was found (relay card {cid}), "
                            "but no live space exists. Not minting silently — resolve with 'conscio space migrate' "
                            "or delete the evidence explicitly."
                        ),
                        repair_pointer=False,
                        announcement="",
                    )
        except Exception:
            pass

        # B6: Fresh mint on durable space (only when nothing exists)
        return SpaceResolution(
            kind="B6",
            target=durable_target,
            reason="",
            repair_pointer=True,
            announcement="",
        )

    # Fallback for unexpected states
    return SpaceResolution(
        kind="unhandled",
        target=None,
        reason=f"unhandled state for slug {slug}",
    )
