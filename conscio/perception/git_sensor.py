"""GitSensor — monitor git log for new commits (stdlib only, subprocess)."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import OrderedDict
from pathlib import Path

from ..risk import Risk
from .sensor import PerceptionFrame, SensorAdapter

_MAX_COMMITS_LIST = 5
_DEFAULT_MAX_SEEN = 10_000
_MAX_GIT_TIMEOUT = 30.0


class GitSensor(SensorAdapter):
    """Read-only pseudo-realtime ``git log`` poller for a single repo.

    Produces ``PerceptionFrame`` with observations like
    ``commit <hash> by <author>: <subject>``. Idempotent via a bounded
    ``_seen`` OrderedDict (LRU eviction at ``max_seen`` entries). When
    the number of new commits exceeds ``_MAX_COMMITS_LIST``, observations
    are summarised.

    Never raises — a missing ``.git``, absent binary or timeout degrades to
    an empty frame. stdlib only; no outbound network.
    """

    name = "git"
    risk = Risk.LOW

    def __init__(self, repo_path: str, *, timeout: float = 5.0,
                 max_seen: int = _DEFAULT_MAX_SEEN) -> None:
        self._repo = Path(repo_path)
        self._timeout = min(timeout, _MAX_GIT_TIMEOUT)
        self._git_bin = shutil.which("git") or "git"
        # Resolve git to absolute path to prevent PATH hijacking
        if self._git_bin != "git":
            self._git_bin = os.path.realpath(self._git_bin)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_seen = max_seen
        self._last_poll: float = 0.0

    # -- SensorAdapter --------------------------------------------------

    def perceive(self) -> PerceptionFrame:
        now = time.time()

        if not shutil.which(self._git_bin):
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

        if not (self._repo / ".git").exists():
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

        # On the first poll, scan the last 24h; afterwards, since the
        # last poll timestamp.
        if self._last_poll > 0:
            since_s = max(1, int(now - self._last_poll))
        else:
            since_s = 86400  # 24h

        try:
            proc = subprocess.run(
                [
                    self._git_bin, "-C", str(self._repo), "log",
                    f"--since={since_s}s",
                    # NUL-delimited to handle commas in author names/subjects
                    "--format=%H%x00%an%x00%s",
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

        if proc.returncode != 0:
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

        new_commits: list[tuple[str, str, str]] = []
        for line in proc.stdout.strip().splitlines():
            # Split on NUL (\x00) — handles commas in author names and subjects
            parts = line.split("\x00")
            if len(parts) < 3:
                continue
            h, author, subject = parts[0], parts[1], parts[2]
            if h in self._seen:
                continue
            self._seen[h] = None
            self._seen.move_to_end(h)  # LRU: most recent at end
            new_commits.append((h, author, subject))

        # Evict oldest entries when exceeding cap
        while len(self._seen) > self._max_seen:
            self._seen.popitem(last=False)  # evict oldest

        self._last_poll = now

        if not new_commits:
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

        # Sort by hash for deterministic output
        new_commits.sort(key=lambda x: x[0])

        if len(new_commits) > _MAX_COMMITS_LIST:
            observations = [f"{len(new_commits)} new commits"]
        else:
            observations: list[str] = []
            for h, author, subject in new_commits:
                observations.append(f"commit {h[:8]} by {author}: {subject}")

        signals = {"commits_new": float(len(new_commits))}
        return PerceptionFrame(
            source=self.name,
            observations=observations,
            signals=signals,
            ts=now,
        )
