"""GitSensor — monitor git log for new commits (stdlib only, subprocess)."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .sensor import PerceptionFrame, SensorAdapter
from ..risk import Risk

_MAX_COMMITS_LIST = 5


class GitSensor(SensorAdapter):
    """Read-only pseudo-realtime ``git log`` poller for a single repo.

    Produces ``PerceptionFrame`` with observations like
    ``commit <hash> by <author>: <subject>``. Idempotent via a ``_seen`` set
    of commit hashes. When the number of new commits exceeds
    ``_MAX_COMMITS_LIST``, observations are summarised.

    Never raises — a missing ``.git``, absent binary or timeout degrades to
    an empty frame. stdlib only; no outbound network.
    """

    name = "git"
    risk = Risk.LOW

    def __init__(self, repo_path: str, *, timeout: float = 5.0) -> None:
        self._repo = Path(repo_path)
        self._timeout = timeout
        self._git_bin = "git"
        self._seen: set[str] = set()
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
                    "--format=%H,%an,%s",
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
            parts = line.split(",", 2)
            if len(parts) < 3:
                continue
            h, author, subject = parts
            if h in self._seen:
                continue
            self._seen.add(h)
            new_commits.append((h, author, subject))

        self._last_poll = now

        if not new_commits:
            return PerceptionFrame(
                source=self.name, observations=[], signals={}, ts=now)

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
