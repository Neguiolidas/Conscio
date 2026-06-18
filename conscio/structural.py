"""StructuralDistiller — Graphify-format ``graph.json`` -> compact ranked signal.

v1.7 "Structural Cognition", slice 1 (the pure core). Takes a Graphify graph
(nodes + links + curated hyperedges, with each node tagged to a community) and
distills it into a small ``StructuralSignal``: the ~24 curated hyperedges plus
per-community summaries — NOT the thousands of raw nodes. A pure ``lookup()``
data layer resolves any node / hyperedge / community id to detail on demand
(v1.7.1 will expose it as ``engine.structural_lookup()``).

**R10 — imported cognition is data, never code.** The graph is parsed with
``json`` only and every field is treated as inert, untrusted data: a node label
that looks like code is stored and returned as a plain string, never evaluated.
There is no ``networkx`` / Graphify runtime dependency and no copied Graphify
source — only its MIT *input format* is consumed.

Source-agnostic: it distills any graph the caller points it at. Workspace scoping
and consent are v1.7.2's job, layered on top of this core.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# OOM backstops (this is consumed on memory-constrained hosts).
DEFAULT_MAX_BYTES = 64 * 1024 * 1024   # global pre-parse cap on the whole file
DEFAULT_MAX_NODES = 200_000            # caps the dominant collection
# (max_hyperedges deferred — bounded by max_bytes; trivial to add if ever needed.)

_MAX_SUMMARY_LABELS = 5   # keep each community summary compact regardless of size
_MAX_SUMMARY_FILES = 5


class StructuralError(ValueError):
    """Malformed or non-Graphify graph input.

    Subclasses ``ValueError`` so callers that ``except ValueError`` keep working.
    """


@dataclass(frozen=True)
class GraphNode:
    """A node projected from the graph — the unit ``lookup()`` returns for ids."""
    id: str
    label: str
    file_type: str
    source_file: str
    source_location: str
    community: Optional[int]


@dataclass(frozen=True)
class Hyperedge:
    """A curated multi-node relation (the already-distilled structural signal)."""
    id: str
    label: str
    nodes: tuple[str, ...]
    relation: str
    confidence_score: float
    source_file: str


@dataclass(frozen=True)
class CommunitySummary:
    """A derived per-community digest (the graph only tags nodes with an int)."""
    community_id: int
    size: int
    top_labels: tuple[str, ...]
    files: tuple[str, ...]


@dataclass(frozen=True)
class StructuralSignal:
    """The distilled output: provenance + counts + hyperedges + community digests.

    Carries the FULL ranked set; how much to inject is the consumer's budget
    decision (v1.7.1), not the distiller's.
    """
    source_path: str
    built_at_commit: str
    content_hash: str
    node_count: int
    link_count: int
    hyperedges: tuple[Hyperedge, ...]
    communities: tuple[CommunitySummary, ...]


class StructuralDistiller:
    """Distills a parsed Graphify graph into a :class:`StructuralSignal`.

    Construct via :meth:`from_path` (a ``graph.json``) or :meth:`from_dict`
    (in-memory). The instance holds the projected nodes/hyperedges and offers
    :meth:`distill` (the compact signal) and :meth:`lookup` (on-demand detail).
    """

    def __init__(
        self,
        *,
        nodes: list[GraphNode],
        hyperedges: list[Hyperedge],
        link_count: int,
        built_at_commit: str,
        source_path: str,
        content_hash: str,
    ) -> None:
        self._nodes = nodes
        self._hyperedges = hyperedges
        self._link_count = link_count
        self._built_at_commit = built_at_commit
        self._source_path = source_path
        self._content_hash = content_hash
        self._by_id = {n.id: n for n in nodes}
        self._he_by_id = {h.id: h for h in hyperedges}
        self._comm_cache: Optional[tuple[CommunitySummary, ...]] = None

    # ── construction ────────────────────────────────────────────────────────────
    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> "StructuralDistiller":
        """Load + validate a ``graph.json``. Size is checked BEFORE parsing."""
        p = Path(path)
        try:
            size = p.stat().st_size
        except OSError as exc:
            raise StructuralError(f"cannot read graph file {p}: {exc}") from exc
        if size > max_bytes:
            raise StructuralError(
                f"graph file is {size} bytes, exceeds max_bytes={max_bytes}")
        raw = p.read_bytes()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StructuralError(f"invalid JSON in {p}: {exc}") from exc
        return cls.from_dict(
            data, source_path=str(p), raw_bytes=raw, max_nodes=max_nodes)

    @classmethod
    def from_dict(
        cls,
        data: Any,
        *,
        source_path: str = "<dict>",
        raw_bytes: Optional[bytes] = None,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> "StructuralDistiller":
        """Validate + project an in-memory graph dict."""
        if not isinstance(data, dict):
            raise StructuralError("graph must be a JSON object")
        if "nodes" not in data and "hyperedges" not in data:
            raise StructuralError(
                "not a Graphify graph: missing both 'nodes' and 'hyperedges'")

        raw_nodes = data.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise StructuralError("'nodes' must be a list")
        raw_hyper = data.get("hyperedges", [])
        if not isinstance(raw_hyper, list):
            raise StructuralError("'hyperedges' must be a list")
        if len(raw_nodes) > max_nodes:
            raise StructuralError(
                f"node count {len(raw_nodes)} exceeds max_nodes={max_nodes}")

        nodes = cls._project_nodes(raw_nodes)
        hyperedges = cls._project_hyperedges(raw_hyper)
        raw_links = data.get("links", [])
        link_count = len(raw_links) if isinstance(raw_links, list) else 0

        if raw_bytes is not None:
            content_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
        else:
            canonical = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
            content_hash = hashlib.sha256(canonical).hexdigest()[:16]

        return cls(
            nodes=nodes,
            hyperedges=hyperedges,
            link_count=link_count,
            built_at_commit=str(data.get("built_at_commit", "")),
            source_path=source_path,
            content_hash=content_hash,
        )

    # ── projection (defensive: bad items skipped, never fatal) ───────────────────
    @staticmethod
    def _project_nodes(raw: list[Any]) -> list[GraphNode]:
        out: list[GraphNode] = []
        for item in raw:
            if not isinstance(item, dict):
                log.warning("structural: skipping non-dict node item")
                continue
            nid = item.get("id")
            if not isinstance(nid, str) or not nid:
                log.warning("structural: skipping node without a string id")
                continue
            comm = item.get("community")
            # bool is an int subclass — never treat True/False as a community.
            community = comm if isinstance(comm, int) and not isinstance(
                comm, bool) else None
            out.append(GraphNode(
                id=nid,
                label=str(item.get("label", "")),
                file_type=str(item.get("file_type", "")),
                source_file=str(item.get("source_file", "")),
                source_location=str(item.get("source_location", "")),
                community=community,
            ))
        return out

    @staticmethod
    def _project_hyperedges(raw: list[Any]) -> list[Hyperedge]:
        out: list[Hyperedge] = []
        for item in raw:
            if not isinstance(item, dict):
                log.warning("structural: skipping non-dict hyperedge item")
                continue
            hid = item.get("id")
            if not isinstance(hid, str) or not hid:
                log.warning("structural: skipping hyperedge without a string id")
                continue
            raw_he_nodes = item.get("nodes", [])
            he_nodes = (
                tuple(n for n in raw_he_nodes if isinstance(n, str))
                if isinstance(raw_he_nodes, list) else ()
            )
            try:
                score = float(item.get("confidence_score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            out.append(Hyperedge(
                id=hid,
                label=str(item.get("label", "")),
                nodes=he_nodes,
                relation=str(item.get("relation", "")),
                confidence_score=score,
                source_file=str(item.get("source_file", "")),
            ))
        return out

    # ── distill + lookup ─────────────────────────────────────────────────────────
    def distill(self) -> StructuralSignal:
        """Produce the compact, fully-ranked structural signal."""
        return StructuralSignal(
            source_path=self._source_path,
            built_at_commit=self._built_at_commit,
            content_hash=self._content_hash,
            node_count=len(self._nodes),
            link_count=self._link_count,
            hyperedges=tuple(self._hyperedges),
            communities=self._communities(),
        )

    def lookup(self, key: str) -> Optional[dict[str, Any]]:
        """Resolve a node / hyperedge / community id to detail; None on miss.

        Resolution order: node id, then hyperedge id, then (for an all-digit key)
        a community id. Node ids win so a node literally named ``"4"`` is never
        shadowed by community 4.
        """
        if not isinstance(key, str):
            return None
        n = self._by_id.get(key)
        if n is not None:
            return {"kind": "node", "id": n.id, "label": n.label,
                    "file_type": n.file_type, "source_file": n.source_file,
                    "source_location": n.source_location, "community": n.community}
        h = self._he_by_id.get(key)
        if h is not None:
            return {"kind": "hyperedge", "id": h.id, "label": h.label,
                    "nodes": list(h.nodes), "relation": h.relation,
                    "confidence_score": h.confidence_score,
                    "source_file": h.source_file}
        if key.lstrip("-").isdigit():
            cid = int(key)
            for c in self._communities():
                if c.community_id == cid:
                    return {"kind": "community", "community_id": c.community_id,
                            "size": c.size, "top_labels": list(c.top_labels),
                            "files": list(c.files)}
        return None

    # ── internals ─────────────────────────────────────────────────────────────────
    def _communities(self) -> tuple[CommunitySummary, ...]:
        if self._comm_cache is not None:
            return self._comm_cache
        groups: dict[int, list[GraphNode]] = {}
        for n in self._nodes:
            if n.community is None:
                continue
            groups.setdefault(n.community, []).append(n)

        summaries: list[CommunitySummary] = []
        for cid, members in groups.items():
            labels: list[str] = []
            for m in members:
                if m.label and m.label not in labels:
                    labels.append(m.label)
                if len(labels) >= _MAX_SUMMARY_LABELS:
                    break
            files: list[str] = []
            for m in members:
                if m.source_file and m.source_file not in files:
                    files.append(m.source_file)
                if len(files) >= _MAX_SUMMARY_FILES:
                    break
            summaries.append(CommunitySummary(
                community_id=cid, size=len(members),
                top_labels=tuple(labels), files=tuple(files)))

        # rank: size desc, community_id asc as a stable tiebreak
        summaries.sort(key=lambda c: (-c.size, c.community_id))
        self._comm_cache = tuple(summaries)
        return self._comm_cache
