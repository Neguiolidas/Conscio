# conscio/content_layer.py
"""
Content Layering — derives a ROUTINE/PROCESSING/INTUITION layer per content item
at query time (no schema change), used as a near-tie tiebreak in recall().

Origin: Noetic Helix layers (Noosphere-Manifold, CC BY-NC-SA 4.0). Operational
paraphrase: factual noise (ROUTINE, N-1), processed insight (PROCESSING, N),
unvalidated hypothesis (INTUITION, N+1).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional


class ContentLayer(Enum):
    ROUTINE = "routine"        # N-1: factual/system noise
    PROCESSING = "processing"  # N:   insights, reflections, decisions
    INTUITION = "intuition"    # N+1: hypotheses, predictions, anomalies


# The CATEGORY carries the layer semantics (ContentStore content_type is only
# prose/code/metric/log — formatting, not layer). Every real VALID_CATEGORY is
# mapped here. content_type is a fallback for unrecognized categories only.
_LAYER_BY_CATEGORY = {
    "reflection": ContentLayer.PROCESSING,
    "consciousness": ContentLayer.PROCESSING,
    "error": ContentLayer.INTUITION,        # anomalies / surprises — unvalidated signal
    "system": ContentLayer.ROUTINE,
    "trading": ContentLayer.ROUTINE,
    "perception": ContentLayer.ROUTINE,
    "session": ContentLayer.ROUTINE,
    "external": ContentLayer.ROUTINE,
}
_ROUTINE_CONTENT_TYPES = {"metric", "log"}


def layer_of(category: str, content_type: str = "") -> ContentLayer:
    """
    Classify content into a layer. The CATEGORY is authoritative — it carries the
    layer semantics (reflections/consciousness → PROCESSING, errors/anomalies →
    INTUITION, system/trading/perception/session/external → ROUTINE). For an
    unrecognized category, a metric/log content_type still reads as ROUTINE;
    otherwise default PROCESSING.
    """
    if category in _LAYER_BY_CATEGORY:
        return _LAYER_BY_CATEGORY[category]
    if content_type in _ROUTINE_CONTENT_TYPES:
        return ContentLayer.ROUTINE
    return ContentLayer.PROCESSING


# Recall reorder constants. RRF rank is higher = better.
LAYER_EPSILON = 0.01    # rank-bucket width (~1% of RRF range); a one-line tunable
_LAYER_PRIORITY = {
    ContentLayer.PROCESSING: 2,
    ContentLayer.INTUITION: 1,
    ContentLayer.ROUTINE: 0,
}


def layer_sort_key(result):
    """
    Sort key for recall results: relevance first (bucketed by LAYER_EPSILON),
    then layer priority within a bucket, then exact rank. Non-destructive — layer
    only reorders near-ties; a high-rank ROUTINE result is never buried.

    `result` is any object exposing `.source_category`, `.content_type`, `.rank`.
    """
    layer = layer_of(result.source_category, result.content_type)
    bucket = int(result.rank / LAYER_EPSILON)
    return (-bucket, -_LAYER_PRIORITY[layer], -result.rank)


class ContentLayerManager:
    """
    Unified content layer managing ContentStore, SessionRAG, and WorldModel.
    
    This class consolidates all content-related operations:
    - recall(): Unified search across ContentStore (FTS5 + layer reorder) and SessionRAG (semantic)
    - perceive(): Update WorldModel with observed state
    
    The engine delegates to this manager for all content operations.
    """
    
    def __init__(
        self,
        content_store,
        world_model,
        session_rag_provider=None,
    ):
        self.content_store = content_store
        self.world_model = world_model
        self._session_rag_provider = session_rag_provider
        self._session_rag = None
    
    @property
    def session_rag(self):
        """Lazily construct SessionRAG via the shared factory provider."""
        if self._session_rag is None and self._session_rag_provider is not None:
            self._session_rag = self._session_rag_provider()
        return self._session_rag
    
    def recall(
        self,
        query: str,
        k: int = 3,
        categories: Optional[list[str]] = None,
    ) -> list[str]:
        """
        Retrieve relevant past context across sessions.
        
        Primary source is ContentStore FTS5 (BM25 + RRF) with layer-prioritized reorder.
        When SessionRAG is available (local Ollama up), semantic hits are merged in.
        Each snippet is length-bounded; results are de-duplicated and capped at k.
        
        Args:
            query: Free-text query (e.g., current world_state + anomalies).
            k: Max snippets to return.
            categories: Optional ContentStore category filter(s).
            
        Returns:
            List of short context snippets (<= ~300 chars each), best first.
        """
        if not query or not query.strip():
            return []
        
        snippets: list[str] = []
        seen: set[str] = set()
        
        def _add(text: str) -> None:
            t = " ".join((text or "").split())[:300]
            key = t[:80].lower()
            if t and key not in seen:
                seen.add(key)
                snippets.append(t)
        
        # ── ContentStore FTS5 (layer-prioritized reorder) ──
        try:
            results = []
            if categories:
                for cat in categories:
                    results.extend(self.content_store.search(query, limit=k, category=cat))
            else:
                results.extend(self.content_store.search(query, limit=k))
            results.sort(key=layer_sort_key)  # near-tie tiebreak by content layer
            for r in results:
                _add(r.content)
        except Exception:
            logging.warning("ContentStore search failed in recall()", exc_info=True)
        
        # ── SessionRAG semantic (optional) ──
        try:
            rag = self.session_rag
            if rag is not None:
                for r in rag.search(query, limit=k):
                    _add(r.content)
        except Exception:
            logging.warning("SessionRAG search failed in recall()", exc_info=True)
        
        return snippets[:k]
    
    def perceive(self, world_state: str, entities: Optional[dict] = None) -> None:
        """
        Update the world model with perceived state.
        
        Args:
            world_state: Text description of current world state
            entities: Dict of {entity_name: {type, state, attributes}} to update
        """
        if entities:
            for name, info in entities.items():
                self.world_model.add_entity(
                    name=name,
                    entity_type=info.get("type", "unknown"),
                    attributes=info.get("attributes"),
                    state=info.get("state", ""),
                )
