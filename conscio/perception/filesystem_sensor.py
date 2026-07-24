"""FilesystemSensor — watch a directory tree via mtime polling (stdlib only)."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .sensor import PerceptionFrame, SensorAdapter
from ..risk import Risk

log = logging.getLogger("conscio.filesystem_sensor")

DEFAULT_IGNORE = (
    ".git", "__pycache__", ".venv", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".tox",
)

_DEFAULT_MAX_BASELINE = 10_000


class FilesystemSensor(SensorAdapter):
    """Read-only mtime poller for a directory tree.

    Produces ``PerceptionFrame`` with observations like ``created: <path>``,
    ``modified: <path>``, ``deleted: <path>``. When the number of changes
    exceeds ``max_files``, observations are summarised to a single line.
    Observations use relative paths (no absolute filesystem leakage).

    Never raises — a missing directory or permission error degrades to an
    empty frame. stdlib only; no outbound network.

    ``max_baseline`` caps the number of tracked files. When the scanned tree
    exceeds this limit, oldest entries (by mtime) are pruned to prevent
    unbounded memory growth on large/long-running watches. Default 10k.
    """

    name = "filesystem"
    risk = Risk.LOW

    def __init__(
        self,
        path: str,
        *,
        depth: int = 3,
        ignorelist: tuple[str, ...] = DEFAULT_IGNORE,
        max_files: int = 50,
        max_baseline: int = _DEFAULT_MAX_BASELINE,
    ) -> None:
        self._path = Path(path)
        self._depth = depth
        self._ignorelist = set(ignorelist)
        self._max_files = max_files
        self._max_baseline = max_baseline
        self._baseline: dict[str, float] = {}
        self._warned = False

    # -- SensorAdapter --------------------------------------------------

    def perceive(self) -> PerceptionFrame:
        now = time.time()

        if not self._path.is_dir():
            if not self._warned:
                log.warning("watch path does not exist: %s", self._path)
                self._warned = True
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

        self._warned = False
        current = self._scan()
        old = self._baseline

        # Prune baseline if exceeding cap (evict oldest by mtime)
        if len(current) > self._max_baseline:
            sorted_items = sorted(current.items(), key=lambda x: x[1])
            keep = dict(sorted_items[-self._max_baseline:])
            pruned = len(current) - len(keep)
            if pruned and not self._warned:
                log.info("baseline pruned %d entries (cap=%d)", pruned, self._max_baseline)
            current = keep

        created: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []

        for p, mtime in current.items():
            if p not in old:
                created.append(p)
            elif mtime > old[p]:
                modified.append(p)

        for p in old:
            if p not in current:
                deleted.append(p)

        self._baseline = current

        total = len(created) + len(modified) + len(deleted)
        if total == 0:
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

        def _rel(p: str) -> str:
            try:
                return str(Path(p).relative_to(self._path))
            except ValueError:
                return p

        # Sort for deterministic output (os.scandir order varies by FS)
        created.sort()
        modified.sort()
        deleted.sort()

        signals: dict[str, float] = {"files_changed": float(total)}

        if total > self._max_files:
            observations = [f"{total} files changed"]
        else:
            observations: list[str] = []
            for p in created:
                observations.append(f"created: {_rel(p)}")
            for p in modified:
                observations.append(f"modified: {_rel(p)}")
            for p in deleted:
                observations.append(f"deleted: {_rel(p)}")

        return PerceptionFrame(
            source=self.name,
            observations=observations,
            signals=signals,
            ts=now,
        )

    # -- internal -------------------------------------------------------

    def _scan(self) -> dict[str, float]:
        result: dict[str, float] = {}
        self._scan_dir(self._path, 0, result)
        return result

    def _scan_dir(
        self, directory: Path, depth: int, out: dict[str, float]
    ) -> None:
        if depth > self._depth:
            return
        try:
            entries = list(os.scandir(directory))
        except (OSError, PermissionError):
            return
        for entry in entries:
            if entry.name in self._ignorelist:
                continue
            if entry.is_dir(follow_symlinks=False):
                self._scan_dir(Path(entry.path), depth + 1, out)
            elif entry.is_file(follow_symlinks=False):
                try:
                    out[str(Path(entry.path))] = entry.stat(
                        follow_symlinks=False).st_mtime
                except OSError:
                    continue
