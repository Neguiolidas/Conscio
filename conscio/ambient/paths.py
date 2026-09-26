# conscio/ambient/paths.py
"""Where the machine's ambient files live. One function per file, so the MCP
surface, the CLI and every reactor name the same board (I1)."""

from __future__ import annotations

from pathlib import Path

from ..liaison import directory


def ambient_dir(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else directory.relay_root()) / "ambient"


def board_path(root: Path | None = None) -> Path:
    return ambient_dir(root) / "board.db"


def flag_path(root: Path | None = None) -> Path:
    return ambient_dir(root) / "enabled"


def registry_path(root: Path | None = None) -> Path:
    return ambient_dir(root) / "agents.json"


def sweep_lock_path(root: Path | None = None) -> Path:
    return ambient_dir(root) / "board.db.sweep.lock"

__all__ = ["ambient_dir", "board_path", "flag_path", "registry_path", "sweep_lock_path"]
