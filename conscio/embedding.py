"""EmbeddingProvider — unified wrapper for Conscio embeddings.

Reuse embedders already present in session_rag (OpenAICompatibleEmbedder, OllamaEmbedder).
Fallback chain: Ollama -> OpenAI compat -> sentence_transformers (optional) -> None.

Default model: nomic-embed-text-v1.5 (768-dim — matches SessionRAG).

NOTE: force_no_network is for testing — disables all network probes,
returns None from all embed calls.
"""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DIMENSION = 768


class EmbeddingProvider:
    """Unified embedder with lazy fallback chain."""

    def __init__(self, force_no_network: bool = False):
        self.default_dimension = DEFAULT_DIMENSION
        self._force_no_network = force_no_network
        self._embedder = None  # injected by tests or auto-probed on first use

    def get_embedder(self):
        """Probe available embedder lazily. Returns None if none available."""
        if self._force_no_network:
            return None
        if self._embedder is not None:
            return self._embedder

        # Try Ollama first (matches existing SessionRAG default)
        try:
            from .session_rag import OllamaEmbedder
            ed = OllamaEmbedder()
            # probe: a tiny embed
            v = ed.embed("test")
            if v and len(v) == self.default_dimension:
                self._embedder = ed
                return ed
        except Exception as e:
            logger.debug(f"OllamaEmbedder unavailable: {e}")

        # Try OpenAI compatible (LM Studio local)
        try:
            from .session_rag import OpenAICompatibleEmbedder
            ed = OpenAICompatibleEmbedder()
            v = ed.embed("test")
            if v and len(v) == self.default_dimension:
                self._embedder = ed
                return ed
        except Exception as e:
            logger.debug(f"OpenAICompatibleEmbedder unavailable: {e}")

        # Try sentence_transformers (optional dep)
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("nomic-embed-text-v1.5")
            v = model.encode("test").tolist()
            if v and len(v) == self.default_dimension:
                self._embedder = model
                return model
        except ImportError:
            logger.debug("sentence_transformers not installed — skipping")
        except Exception as e:
            logger.debug(f"sentence_transformers failed: {e}")

        return None

    def available(self) -> bool:
        """Return True if any embedder is available."""
        return self.get_embedder() is not None

    def embed(self, text: str) -> Optional[list[float]]:
        """Embed text. Returns None if no embedder available."""
        ed = self.get_embedder()
        if ed is None:
            return None
        try:
            v = ed.embed(text)
            return list(v) if v is not None else None
        except Exception as e:
            logger.warning(f"embed failed: {e}")
            return None

    def embed_batch(self, texts: list[str]) -> Optional[list[list[float]]]:
        """Batch embed. Returns None if no embedder available."""
        ed = self.get_embedder()
        if ed is None:
            return None
        try:
            if hasattr(ed, "embed_batch"):
                vecs = ed.embed_batch(texts)
                return [list(v) for v in vecs] if vecs else None
            # Fallback: per-item
            return [list(ed.embed(t)) for t in texts]
        except Exception as e:
            logger.warning(f"embed_batch failed: {e}")
            return None
