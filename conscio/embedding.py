"""EmbeddingProvider — unified wrapper for Conscio embeddings.

Fallback chain: Ollama -> OpenAI compat -> sentence_transformers (native) -> None.

Default model: all-MiniLM-L6-v2 (384-dim, ~90MB, native in-process).
Optional model: nomic-embed-text-v1.5 (768-dim, ~600MB) via CONSCIO_EMBED_MODEL env var.

Set CONSCIO_EMBED_MODEL=nomic-embed-text-v1.5 to use the 768-dim model.
Set CONSCIO_EMBED_DIM=768 to match the dimension.

NOTE: force_no_network is for testing — disables all network probes,
returns None from all embed calls.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_DIMENSION = 384  # all-MiniLM-L6-v2 dimension

# Optional 768-dim model (set CONSCIO_EMBED_MODEL env var)
LARGE_MODEL = "nomic-embed-text-v1.5"
LARGE_DIMENSION = 768


def _resolve_model() -> tuple[str, int]:
    """Resolve model name + dimension from env vars or defaults."""
    model = os.environ.get("CONSCIO_EMBED_MODEL", DEFAULT_MODEL)
    dim = int(os.environ.get("CONSCIO_EMBED_DIM", "0"))
    if dim == 0:
        dim = LARGE_DIMENSION if model == LARGE_MODEL else DEFAULT_DIMENSION
    return model, dim


class EmbeddingProvider:
    """Unified embedder with lazy fallback chain.

    Fallback order:
    1. Ollama (if running locally)
    2. OpenAI-compatible API (LM Studio, etc.)
    3. sentence_transformers (NATIVE, no daemon — default: all-MiniLM-L6-v2)
    4. None

    The sentence_transformers fallback is truly self-contained: loads the model
    from HF cache (no network needed after first download), runs in-process.

    Default: all-MiniLM-L6-v2 (384-dim, ~90MB cached).
    Optional: nomic-embed-text-v1.5 (768-dim, ~600MB) via CONSCIO_EMBED_MODEL env.
    """

    def __init__(self, force_no_network: bool = False):
        model_name, dim = _resolve_model()
        self.model_name = model_name
        self.default_dimension = dim
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

        # Try sentence_transformers (NATIVE, no daemon)
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(self.model_name)
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
            if hasattr(ed, "encode") and not hasattr(ed, "embed"):
                v = ed.encode(text)  # type: ignore[union-attr]  # type: ignore[union-attr]
                return list(v) if v is not None else None
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
            if hasattr(ed, "encode") and not hasattr(ed, "embed"):
                vecs = ed.encode(texts)  # type: ignore[union-attr]
                return [list(v) for v in vecs] if vecs is not None else None
            if hasattr(ed, "embed_batch"):
                vecs = ed.embed_batch(texts)
                return [list(v) for v in vecs] if vecs else None
            return [list(ed.embed(t)) for t in texts]
        except Exception as e:
            logger.warning(f"embed_batch failed: {e}")
            return None
