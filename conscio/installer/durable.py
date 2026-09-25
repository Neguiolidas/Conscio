"""Durable host-bound space resolution (Conscio v4.8 S1).

Provides pure space resolution according to host environment and storage binding,
supporting seamless plugin survival across uninstalls, downgrades, and reinstalls.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from ..mcp.host_identity import derive_host_identity
from .binding import unexpanded_variable
from .spaces import minting_lock, slugify, space_dir

__all__ = [
    "SpaceResolution",
    "find_refused_markers",
    "known_plugin_data_dirs",
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
    slug: str = ""
    runtime: str = ""


def plugin_data_roots(env: Mapping[str, str] | None = None) -> list[Path]:
    """Return recognized plugin data roots present in the environment."""
    if env is None:
        env = os.environ
    roots: list[Path] = []
    for key in _SUPPORTED_PLUGIN_ROOT_KEYS:
        val = env.get(key)
        if val:
            if unexpanded_variable(val):
                logger.warning(
                    "ignoring plugin data env var %s containing unexpanded variable: %r",
                    key,
                    val,
                )
                continue
            p_val = Path(val).expanduser()
            if not p_val.is_absolute():
                logger.warning(
                    "ignoring plugin data env var %s with non-absolute path: %r",
                    key,
                    val,
                )
                continue
            roots.append(p_val)
    return roots


def known_plugin_data_dirs(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    only_existing: bool = True,
) -> list[Path]:
    """Return recognized plugin data directories from env and standard filesystem locations.

    Checks:
    - Environment variables: CLAUDE_PLUGIN_DATA, ZCODE_PLUGIN_DATA (via plugin_data_roots)
    - Standard filesystem defaults relative to home (Path.home()):
      ~/.claude/plugins/data/conscio-conscio
      ~/.zcode/cli/plugins/data/conscio@conscio
    (Codex is treated as absent until upstream documentation defines it).

    If only_existing is True (default), returns only paths that exist on disk.
    Preserves order and deduplicates paths.
    """
    if env is None:
        env = os.environ
    if home is None:
        home = Path.home()

    candidates: list[Path] = []

    # 1. From env vars
    candidates.extend(plugin_data_roots(env))

    # 2. Standard filesystem paths relative to home
    candidates.append(home / ".claude" / "plugins" / "data" / "conscio-conscio")
    candidates.append(home / ".zcode" / "cli" / "plugins" / "data" / "conscio@conscio")

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[Path] = []
    for p in candidates:
        try:
            norm = str(p.expanduser().resolve())
        except (ValueError, OSError):
            norm = str(p.expanduser())
        if norm in seen:
            continue
        seen.add(norm)
        if not only_existing or p.is_dir():
            result.append(p)
    return result


def find_refused_markers(
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[dict]:
    """Scan known plugin data directories for space-refused.json markers."""
    markers: list[dict] = []
    for p_dir in known_plugin_data_dirs(env=env, home=home, only_existing=True):
        marker_path = p_dir / "space-refused.json"
        if marker_path.is_file():
            try:
                data = json.loads(marker_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            st = marker_path.stat()
            markers.append({
                "path": marker_path,
                "plugin_dir": p_dir,
                "state": data.get("state", "unknown"),
                "reason": data.get("reason", ""),
                "ts": float(data.get("ts", st.st_mtime)),
                "mtime": st.st_mtime,
            })
    return markers


def plugin_data_dir(storage: Path | str, env: Mapping[str, str] | None = None) -> Path | None:
    """Return the base plugin data directory for storage, or None if not plugin-bound."""
    storage_path = Path(storage).expanduser()
    roots = known_plugin_data_dirs(env=env, only_existing=False)
    for r in roots:
        try:
            if storage_path == r or storage_path.is_relative_to(r):
                return r
        except (ValueError, OSError):
            continue
    return None


def plugin_pointer_path(storage: Path | str, env: Mapping[str, str] | None = None) -> Path | None:
    """Return the space-pointer.json path for storage, or None if not plugin-bound."""
    p_dir = plugin_data_dir(storage, env)
    return (p_dir / "space-pointer.json") if p_dir is not None else None


def plugin_refused_marker_path(storage: Path | str, env: Mapping[str, str] | None = None) -> Path | None:
    """Return the space-refused.json path for storage, or None if not plugin-bound."""
    p_dir = plugin_data_dir(storage, env)
    return (p_dir / "space-refused.json") if p_dir is not None else None


def write_refused_marker(
    storage: Path | str,
    state: str,
    reason: str,
    env: Mapping[str, str] | None = None,
    ts: float | None = None,
) -> Path | None:
    """Atomically write space-refused.json schema 1, or None if not plugin-bound."""
    marker_path = plugin_refused_marker_path(storage, env)
    if marker_path is None:
        return None
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
    """Remove space-refused.json if present in plugin data directory."""
    marker_path = plugin_refused_marker_path(storage, env)
    if marker_path is not None:
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
            slug=slug,
            runtime=runtime,
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
                    slug=slug,
                    runtime=runtime,
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
                slug=slug,
                runtime=runtime,
            )
        except Exception as exc:
            return SpaceResolution(
                kind="B5",
                target=None,
                reason=f"corrupt space pointer at {pointer_file}: {exc}",
                repair_pointer=False,
                announcement="",
                slug=slug,
                runtime=runtime,
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
            slug=slug,
            runtime=runtime,
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
            slug=slug,
            runtime=runtime,
        )

    if plugin_has_instance and durable_has_instance:
        # B3: Two space copies found
        plugin_id = None
        plugin_unreadable = False
        durable_id = None
        durable_unreadable = False
        if plugin_inst_path is not None:
            try:
                p_data = json.loads(plugin_inst_path.read_text(encoding="utf-8"))
                plugin_id = str(p_data.get("instance_id", ""))
            except Exception:
                plugin_unreadable = True
        try:
            d_data = json.loads(durable_inst_path.read_text(encoding="utf-8"))
            durable_id = str(d_data.get("instance_id", ""))
        except Exception:
            durable_unreadable = True

        if plugin_id and durable_id and plugin_id == durable_id:
            reason = (
                f"two copies of the same identity for {slug}; "
                "they may have diverged (interrupted cross-fs copy or manual copy)"
            )
        else:
            if plugin_unreadable:
                p_id8 = "unreadable"
            elif plugin_id:
                p_id8 = plugin_id[:8]
            else:
                p_id8 = "unknown"

            if durable_unreadable:
                d_id8 = "unreadable"
            elif durable_id:
                d_id8 = durable_id[:8]
            else:
                d_id8 = "unknown"

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
            slug=slug,
            runtime=runtime,
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
                slug=slug,
                runtime=runtime,
            )

        try:
            from ..liaison import directory
            for card in directory.peers():
                if directory.is_remote(card):
                    continue
                if card.get("runtime") == runtime:
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
                        slug=slug,
                        runtime=runtime,
                    )
        except Exception as exc:
            exc_type = type(exc).__name__
            return SpaceResolution(
                kind="B4",
                target=None,
                reason=(
                    f"could not check the relay directory for a previous identity of {slug} "
                    f"({exc_type}: {exc}); not minting silently — fix access to "
                    "~/.conscio/relay/peers or run 'conscio space migrate'."
                ),
                repair_pointer=False,
                announcement="",
                slug=slug,
                runtime=runtime,
            )

        # B6: Fresh mint on durable space (only when nothing exists)
        return SpaceResolution(
            kind="B6",
            target=durable_target,
            reason="",
            repair_pointer=True,
            announcement="",
            slug=slug,
            runtime=runtime,
        )

    # Fallback for unexpected states
    return SpaceResolution(
        kind="unhandled",
        target=None,
        reason=f"unhandled state for slug {slug}",
        slug=slug,
        runtime=runtime,
    )
