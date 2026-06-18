"""Structural Drift — temporal awareness over an ingested structure (v1.8.0).

The distiller (v1.7.0) ingests a *snapshot* of the code the agent lives in; that
snapshot is timeless. This module makes it temporal — the agent notices when its
structural map has moved or gone stale. It is a pure, fail-tolerant sibling of
:mod:`conscio.structural_consent`, layered *below* the engine (no engine import).

Two distinct signals, kept separate on purpose:

- **Drift** — *did the graph change since I last looked?* The last distilled
  signal is persisted per ``Workspace.id`` as a small :class:`StructuralDigest`;
  :func:`compute_delta` compares a fresh signal against it.
- **Freshness** — *is the graph behind the actual repo right now?* The graph's
  ``built_at_commit`` is compared against the repo's current HEAD, read **purely**
  from ``.git`` by :func:`read_head_commit` — no ``subprocess``, no ``git``
  invocation (R10 by spirit: pure data + stdlib only). The commit *distance*
  ("N behind") is intentionally NOT computed — ``is_stale`` and the count drive
  the identical remedy (re-distill), so the number is noise, not signal.

Every surface degrades gracefully: a corrupt store, an unreadable ``.git``, a
non-repo workspace must never raise into a caller's loop.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .structural import StructuralSignal
from .timeutil import naive_utcnow

log = logging.getLogger(__name__)

# Where the per-workspace baseline is persisted, relative to engine storage.
DRIFT_FILENAME = "structural_drift.json"

# A git object name: 7–40 hex chars (abbrev through full sha-1).
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def drift_path(storage: str | Path) -> Path:
    """The drift-store path for an engine storage dir (CLI + daemon agree)."""
    return Path(storage) / DRIFT_FILENAME


# ── persisted baseline ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StructuralDigest:
    """A small fingerprint of a distilled signal — the persisted baseline.

    Carries enough to diff topology without storing the whole signal: the
    provenance (commit/hash), the counts, and id→label / id→size maps for the
    hyperedges and communities. Size is bounded by the graph's own counts (the
    distiller returns ALL communities ranked, not a top-K cut), which is itself
    bounded by the caller's ``max_nodes`` — so no extra cap is needed here.
    """
    commit: str
    content_hash: str
    node_count: int
    link_count: int
    hyperedges: dict[str, str]      # hyperedge id -> label
    communities: dict[str, int]     # community id (as str) -> size
    seen_at: str                    # naive UTC iso, when this baseline was recorded

    @classmethod
    def from_signal(cls, sig: StructuralSignal) -> "StructuralDigest":
        return cls(
            commit=sig.built_at_commit,
            content_hash=sig.content_hash,
            node_count=sig.node_count,
            link_count=sig.link_count,
            hyperedges={h.id: h.label for h in sig.hyperedges},
            communities={str(c.community_id): c.size for c in sig.communities},
            seen_at=naive_utcnow().isoformat(),
        )

    def to_json(self) -> dict:
        return {
            "commit": self.commit,
            "content_hash": self.content_hash,
            "node_count": self.node_count,
            "link_count": self.link_count,
            "hyperedges": dict(self.hyperedges),
            "communities": dict(self.communities),
            "seen_at": self.seen_at,
        }

    @classmethod
    def from_json(cls, d: object) -> Optional["StructuralDigest"]:
        """Rebuild from a stored dict; None if malformed (fail-tolerant)."""
        if not isinstance(d, dict):
            return None
        try:
            return cls(
                commit=str(d["commit"]),
                content_hash=str(d["content_hash"]),
                node_count=int(d["node_count"]),
                link_count=int(d["link_count"]),
                hyperedges={str(k): str(v)
                            for k, v in dict(d.get("hyperedges") or {}).items()},
                communities={str(k): int(v)
                             for k, v in dict(d.get("communities") or {}).items()},
                seen_at=str(d.get("seen_at", "")),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return None


# ── prev → current delta ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class StructuralDelta:
    """The difference between a persisted baseline and a fresh signal."""
    first_sight: bool
    commit_changed: bool
    commit_from: str
    commit_to: str
    hash_changed: bool
    node_delta: int
    link_delta: int
    hyperedges_added: tuple[str, ...]                       # labels (for display)
    hyperedges_removed: tuple[str, ...]                     # labels
    communities_added: tuple[int, ...]
    communities_removed: tuple[int, ...]
    communities_resized: tuple[tuple[int, int, int], ...]   # (id, old_size, new_size)

    @property
    def changed(self) -> bool:
        """Any real topology/content difference. False on first sight."""
        if self.first_sight:
            return False
        return bool(
            self.commit_changed or self.hash_changed
            or self.node_delta or self.link_delta
            or self.hyperedges_added or self.hyperedges_removed
            or self.communities_added or self.communities_removed
            or self.communities_resized)

    @property
    def summary(self) -> str:
        if self.first_sight:
            return "first sighting of this workspace's structure"
        if not self.changed:
            return "structure unchanged"
        parts: list[str] = []
        if self.commit_changed:
            parts.append(
                f"commit {(self.commit_from or '-')[:8]}->{(self.commit_to or '-')[:8]}")
        if self.node_delta:
            parts.append(f"nodes {self.node_delta:+d}")
        if self.link_delta:
            parts.append(f"links {self.link_delta:+d}")
        if self.hyperedges_added:
            parts.append(f"+{len(self.hyperedges_added)} hyperedge(s)")
        if self.hyperedges_removed:
            parts.append(f"-{len(self.hyperedges_removed)} hyperedge(s)")
        if self.communities_added:
            parts.append(f"+{len(self.communities_added)} community(ies)")
        if self.communities_removed:
            parts.append(f"-{len(self.communities_removed)} community(ies)")
        if self.communities_resized:
            parts.append(f"{len(self.communities_resized)} community(ies) resized")
        if not parts and self.hash_changed:
            parts.append("content changed")
        return "; ".join(parts)

    def to_advisory(self) -> dict:
        return {
            "first_sight": self.first_sight,
            "changed": self.changed,
            "commit_changed": self.commit_changed,
            "hash_changed": self.hash_changed,
            "node_delta": self.node_delta,
            "link_delta": self.link_delta,
            "hyperedges_added": list(self.hyperedges_added),
            "hyperedges_removed": list(self.hyperedges_removed),
            "communities_added": list(self.communities_added),
            "communities_removed": list(self.communities_removed),
            "communities_resized": [list(t) for t in self.communities_resized],
            "summary": self.summary,
        }


def compute_delta(
    prev: Optional[StructuralDigest], signal: StructuralSignal
) -> StructuralDelta:
    """Compare a persisted baseline against a fresh signal (PURE).

    Hyperedges and communities are diffed by **id** (a relabel is not structural
    drift); added/removed hyperedges are reported as labels for display.
    """
    if prev is None:
        return StructuralDelta(
            first_sight=True, commit_changed=False,
            commit_from="", commit_to=signal.built_at_commit,
            hash_changed=False, node_delta=0, link_delta=0,
            hyperedges_added=(), hyperedges_removed=(),
            communities_added=(), communities_removed=(), communities_resized=())

    he_now = {h.id: h.label for h in signal.hyperedges}
    he_prev = prev.hyperedges
    he_added = tuple(he_now[hid] or hid for hid in he_now if hid not in he_prev)
    he_removed = tuple(he_prev[hid] or hid for hid in he_prev if hid not in he_now)

    comm_now = {c.community_id: c.size for c in signal.communities}
    comm_prev: dict[int, int] = {}
    for k, v in prev.communities.items():
        try:
            comm_prev[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    comm_added = tuple(sorted(cid for cid in comm_now if cid not in comm_prev))
    comm_removed = tuple(sorted(cid for cid in comm_prev if cid not in comm_now))
    comm_resized = tuple(sorted(
        (cid, comm_prev[cid], comm_now[cid])
        for cid in comm_now if cid in comm_prev and comm_prev[cid] != comm_now[cid]))

    return StructuralDelta(
        first_sight=False,
        commit_changed=(prev.commit != signal.built_at_commit),
        commit_from=prev.commit, commit_to=signal.built_at_commit,
        hash_changed=(prev.content_hash != signal.content_hash),
        node_delta=signal.node_count - prev.node_count,
        link_delta=signal.link_count - prev.link_count,
        hyperedges_added=he_added, hyperedges_removed=he_removed,
        communities_added=comm_added, communities_removed=comm_removed,
        communities_resized=comm_resized)


# ── freshness vs the repo HEAD (pure .git read) ───────────────────────────────
@dataclass(frozen=True)
class StructuralFreshness:
    """Graph commit vs the repo's current HEAD."""
    graph_commit: str
    head_commit: Optional[str]      # None when .git is unreadable / not a repo

    @property
    def known(self) -> bool:
        return self.head_commit is not None and bool(self.graph_commit)

    @property
    def is_stale(self) -> bool:
        """True only when both commits are known AND they differ.

        Comparison is exact (Graphify writes a full sha; git HEAD is a full sha);
        a length/format mismatch is treated as stale — the conservative default,
        since "I cannot confirm they match" should bias toward re-distilling.
        """
        return self.known and self.head_commit != self.graph_commit

    def to_advisory(self) -> dict:
        return {
            "graph_commit": self.graph_commit,
            "head_commit": self.head_commit,
            "known": self.known,
            "stale": self.is_stale,
        }


def _clean_sha(value: object) -> Optional[str]:
    s = str(value or "").strip()
    return s if _SHA_RE.match(s) else None


def read_head_commit(root: str | Path) -> Optional[str]:
    """Read the current HEAD commit sha from ``root/.git`` — PURE, never raises.

    Handles a normal ``.git`` directory, a detached HEAD, a ``packed-refs``
    fallback, and a ``.git`` *file* (``gitdir: <path>``, as used by worktrees).
    Returns None on anything unreadable, malformed, or not-a-repo.
    """
    try:
        git = Path(root) / ".git"
        if git.is_file():
            text = git.read_text().strip()
            if not text.startswith("gitdir:"):
                return None
            target = Path(text[len("gitdir:"):].strip())
            git = target if target.is_absolute() else (Path(root) / target).resolve()
        if not git.is_dir():
            return None

        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return _clean_sha(head)             # detached HEAD

        ref = head[4:].strip()                  # e.g. refs/heads/main
        loose = git / ref
        if loose.is_file():
            return _clean_sha(loose.read_text())
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name.strip() == ref:
                    return _clean_sha(sha)
        return None
    except OSError:
        return None


def compute_freshness(root: str | Path, graph_commit: str) -> StructuralFreshness:
    """Freshness of a graph (built at ``graph_commit``) vs the repo at ``root``."""
    return StructuralFreshness(
        graph_commit=graph_commit or "", head_commit=read_head_commit(root))


# ── persisted store (mirrors StructuralConsent) ───────────────────────────────
class StructuralDriftStore:
    """Per-``Workspace.id`` baseline digests, persisted as a small JSON map.

    Tolerant of a missing or corrupt store (treated as "no baseline for anyone")
    and of a failed write (logged, never raised) — drift tracking must never
    break the daemon loop it rides on.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._map: dict[str, StructuralDigest] = self._load()

    def _load(self) -> dict[str, StructuralDigest]:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, StructuralDigest] = {}
        for k, v in raw.items():
            digest = StructuralDigest.from_json(v)
            if digest is not None:
                out[str(k)] = digest
        return out

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(
                {k: d.to_json() for k, d in self._map.items()}, indent=1))
        except OSError as exc:
            log.warning("structural drift store save failed: %s", exc)

    def get(self, workspace_id: str) -> Optional[StructuralDigest]:
        return self._map.get(workspace_id)

    def put(self, workspace_id: str, digest: StructuralDigest) -> None:
        """Advance the baseline for a workspace; persists (write failure swallowed)."""
        self._map[workspace_id] = digest
        self._save()
