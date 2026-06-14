"""
Graphify Bridge — Integrates Graphify knowledge graphs into Conscio RAG.

Graphify (https://github.com/safishamsi/graphify) generates static knowledge
graphs from codebases.  This module loads its output and indexes the graph
into Conscio's ContentStore so that codebase structure, entity relationships,
and architectural patterns become searchable via FTS5/BM25.

Conscio works **without** Graphify — this bridge is optional.  When a
graphify-out directory is provided, the bridge enriches the ContentStore
with structured codebase knowledge that dramatically improves RAG quality
for code-related queries.

Usage::

    from conscio.graphify_bridge import GraphifyBridge

    bridge = GraphifyBridge("/path/to/graphify-out")
    if bridge.available():
        bridge.index_all(content_store)

Requirements:
    - Python 3.10+ (stdlib only — no external deps)
    - Graphify output directory containing graph.json and GRAPH_REPORT.md
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .content_store import ContentStore

logger = logging.getLogger(__name__)


class GraphifyBridge:
    """
    Loads Graphify output and indexes it into a Conscio ContentStore.

    The bridge is designed to be complementary and non-intrusive:
    - No hard dependency on Graphify (graceful unavailability)
    - Deduplicates via content hashing (re-indexing is idempotent)
    - Categories use "external" to avoid polluting consciousness events
    - All graph data is read-only — never mutates the graphify output
    """

    def __init__(self, graphify_dir: str | Path):
        self.dir = Path(graphify_dir)
        self._graph_path = self.dir / "graph.json"
        self._report_path = self.dir / "GRAPH_REPORT.md"

    def available(self) -> bool:
        """True if the graphify output directory exists and has required files."""
        return self._graph_path.exists() and self._report_path.exists()

    def index_all(self, store: "ContentStore") -> dict[str, int]:
        """
        Index all graphify data into the ContentStore.

        Returns a summary dict with counts of indexed items.
        Safe to call multiple times (deduplicates via content hash).
        """
        if not self.available():
            logger.warning("Graphify output not available at %s", self.dir)
            return {"communities": 0, "hyperedges": 0, "report": 0}

        stats = {"communities": 0, "hyperedges": 0, "report": 0}

        # 1. Index the full report as a single searchable document
        stats["report"] = self._index_report(store)

        # 2. Index communities as individual chunks
        stats["communities"] = self._index_communities(store)

        # 3. Index hyperedges (architectural patterns)
        stats["hyperedges"] = self._index_hyperedges(store)

        total = sum(stats.values())
        logger.info(
            "Graphify bridge: indexed %d items (%d communities, %d hyperedges, %d report)",
            total, stats["communities"], stats["hyperedges"], stats["report"],
        )
        return stats

    def _index_report(self, store: "ContentStore") -> int:
        """Index GRAPH_REPORT.md as a codebase overview document."""
        content = self._report_path.read_text(encoding="utf-8")
        store.index(
            label="graphify:codebase-overview",
            content=content,
            category="external",
            content_type="prose",
            chunk_size=3000,
        )
        return 1

    def _index_communities(self, store: "ContentStore") -> int:
        """Index each community as a searchable chunk with its nodes and edges."""
        graph = self._load_graph()
        if not graph:
            return 0

        nodes = graph.get("nodes", [])
        links = graph.get("links", [])

        # Group nodes by community
        communities: dict[int, list[dict]] = {}
        for node in nodes:
            cid = node.get("community", -1)
            communities.setdefault(cid, []).append(node)

        # Group links by source community
        node_community: dict[str, int] = {
            n["id"]: n.get("community", -1) for n in nodes
        }
        community_links: dict[int, list[dict]] = {}
        for link in links:
            src_cid = node_community.get(link.get("source", ""), -1)
            if src_cid >= 0:
                community_links.setdefault(src_cid, []).append(link)

        count = 0
        for cid, c_nodes in communities.items():
            if cid < 0:
                continue

            # Build searchable text for this community
            parts = [f"Community {cid}:"]
            for n in c_nodes[:50]:  # Cap at 50 nodes to keep chunks reasonable
                label = n.get("label", n.get("norm_label", ""))
                src = n.get("source_file", "")
                loc = n.get("source_location", "")
                parts.append(f"  - {label} ({src}:{loc})")

            # Add relationships
            c_links = community_links.get(cid, [])
            if c_links:
                parts.append("Relationships:")
                for lnk in c_links[:30]:
                    src = lnk.get("source", "")
                    tgt = lnk.get("target", "")
                    rel = lnk.get("relation", "")
                    parts.append(f"  - {src} --[{rel}]--> {tgt}")

            content = "\n".join(parts)
            # Use first node's source file as label hint
            primary_file = c_nodes[0].get("source_file", "unknown") if c_nodes else "unknown"
            store.index(
                label=f"graphify:community:{cid}:{primary_file}",
                content=content,
                category="external",
                content_type="code",
                chunk_size=3000,
            )
            count += 1

        return count

    def _index_hyperedges(self, store: "ContentStore") -> int:
        """Index hyperedges as architectural pattern documents."""
        graph = self._load_graph()
        if not graph:
            return 0

        hyperedges = graph.get("hyperedges", [])
        if not hyperedges:
            # Try top-level
            hyperedges = self._raw.get("hyperedges", [])

        count = 0
        for he in hyperedges:
            label = he.get("label", "unnamed pattern")
            relation = he.get("relation", "")
            confidence = he.get("confidence", "")
            score = he.get("confidence_score", 0)
            source = he.get("source_file", "")
            nodes = he.get("nodes", [])

            parts = [
                f"Architectural Pattern: {label}",
                f"Relation: {relation}",
                f"Confidence: {confidence} ({score})",
                f"Source: {source}",
                f"Components ({len(nodes)}):",
            ]
            for n in nodes:
                parts.append(f"  - {n}")

            content = "\n".join(parts)
            store.index(
                label=f"graphify:pattern:{he.get('id', 'unknown')}",
                content=content,
                category="external",
                content_type="code",
                chunk_size=2000,
            )
            count += 1

        return count

    def _load_graph(self) -> dict | None:
        """Load and cache graph.json."""
        if hasattr(self, "_graph_cache"):
            return self._graph_cache

        try:
            raw = self._graph_path.read_text(encoding="utf-8")
            self._raw = json.loads(raw)
            # Nodes and links are at top level, hyperedges may be nested
            self._graph_cache = {
                "nodes": self._raw.get("nodes", []),
                "links": self._raw.get("links", []),
                "hyperedges": (
                    self._raw.get("graph", {}).get("hyperedges", [])
                    or self._raw.get("hyperedges", [])
                ),
            }
            return self._graph_cache
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load graph.json: %s", exc)
            self._graph_cache = None
            return None


def auto_index_graphify(
    store: "ContentStore",
    graphify_dir: str | Path | None = None,
) -> dict[str, int]:
    """
    Convenience function: auto-detect and index graphify output.

    Search order:
    1. Explicit graphify_dir argument
    2. GRAPHIFY_DIR environment variable
    3. <project_root>/graphify-out (auto-detected from conscio location)

    Returns stats dict, or empty dict if not available.
    """
    import os

    candidates = []
    if graphify_dir:
        candidates.append(Path(graphify_dir))

    env_dir = os.environ.get("GRAPHIFY_DIR")
    if env_dir:
        candidates.append(Path(env_dir))

    # Auto-detect: look relative to conscio package
    import conscio as _pkg
    pkg_dir = Path(_pkg.__file__).parent.parent
    candidates.append(pkg_dir / "graphify-out")
    candidates.append(pkg_dir.parent / "graphify-out")

    for candidate in candidates:
        bridge = GraphifyBridge(candidate)
        if bridge.available():
            return bridge.index_all(store)

    logger.debug("No graphify output found in any candidate path")
    return {}
