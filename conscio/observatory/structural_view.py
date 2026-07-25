"""Read-only projection of structural drift + freshness for the Observatory.

Mirrors the Projection/SocietyProjection pattern: opens SQLite/JSON read-only,
never writes. Falls back to empty when files are absent or corrupt.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..guards import clamp_int
from ..structural_drift import (
    StructuralDriftStore,
    drift_path,
    compute_freshness,
)


class StructuralProjection:
    """Read-only view of structural drift store + graph freshness.

    Initialized with the instance storage dir (same as Projection).
    Reads drift_path(storage) for the drift store and compute_freshness()
    for the staleness check. Never writes.
    """

    def __init__(self, storage: Path) -> None:
        self.storage = Path(storage)

    def drift_timeline(self, *, limit: int = 20) -> list[dict]:
        """Return drift entries ordered by seen_at DESC.

        StructuralDriftStore has no get_all() — iterate _map internal.
        Each entry: {workspace_id, seen_at, commit, node_count, link_count}.
        """
        limit = clamp_int(limit, 1, 500)
        path = drift_path(self.storage)
        if not path.exists():
            return []
        try:
            store = StructuralDriftStore(path)
        except Exception:
            return []
        entries: list[dict] = []
        for ws_id, digest in store._map.items():
            entries.append({
                "workspace_id": ws_id,
                "seen_at": getattr(digest, "seen_at", ""),
                "commit": getattr(digest, "commit", ""),
                "node_count": getattr(digest, "node_count", 0),
                "link_count": getattr(digest, "link_count", 0),
            })
        entries.sort(key=lambda e: e.get("seen_at", ""), reverse=True)
        return entries[:limit]

    def freshness(self, *, root: str | None = None) -> dict:
        """Return structural freshness for the workspace at ``root``.

        Returns {known, is_stale, graph_commit, head_commit}.
        If root is None, returns {known: False, ...}.
        """
        if root is None:
            return {"known": False, "is_stale": False,
                    "graph_commit": "", "head_commit": None}
        path = drift_path(self.storage)
        if not path.exists():
            return {"known": False, "is_stale": False,
                    "graph_commit": "", "head_commit": None}
        try:
            store = StructuralDriftStore(path)
        except Exception:
            return {"known": False, "is_stale": False,
                    "graph_commit": "", "head_commit": None}
        if not store._map:
            return {"known": False, "is_stale": False,
                    "graph_commit": "", "head_commit": None}
        first_digest = next(iter(store._map.values()))
        graph_commit = getattr(first_digest, "commit", "")
        try:
            fr = compute_freshness(root, graph_commit)
            adv = fr.to_advisory()
            # to_advisory returns {known, stale, graph_commit, head_commit}
            # normalize is_stale -> stale for API consistency
            return {"known": adv["known"], "is_stale": adv["stale"],
                    "graph_commit": adv["graph_commit"],
                    "head_commit": adv["head_commit"]}
        except Exception:
            return {"known": False, "is_stale": False,
                    "graph_commit": graph_commit, "head_commit": None}

    def graph(self, *, root: str | None = None) -> dict:
        """Return graph.json content if present at root/graphify-out/graph.json.

        Returns {available: False, reason: "..."} when absent.
        GRAPH_RELPATH is relative to workspace root, not storage dir.
        If root is None, tries storage / GRAPH_RELPATH as fallback.
        """
        from ..structural_consent import GRAPH_RELPATH
        if root is not None:
            graph_path = Path(root) / GRAPH_RELPATH
        else:
            graph_path = self.storage / GRAPH_RELPATH
        if not graph_path.exists():
            return {"available": False, "reason": "graph.json not found"}
        try:
            raw = json.loads(graph_path.read_text())
        except Exception as exc:
            return {"available": False,
                    "reason": f"parse error: {type(exc).__name__}"}
        return {"available": True, "data": raw}
