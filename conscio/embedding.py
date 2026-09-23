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

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_DIMENSION = 384  # all-MiniLM-L6-v2 dimension

# Optional 768-dim model (set CONSCIO_EMBED_MODEL env var)
LARGE_MODEL = "nomic-embed-text-v1.5"
LARGE_DIMENSION = 768


VALID_BACKENDS = ("native", "ollama", "openai", "auto")


def resolve_embed_backend(backend: str | None = None) -> str:
    """Resolve backend name from parameter or CONSCIO_EMBED_BACKEND env var.

    Unset / empty defaults to 'native'.
    Valid: 'native', 'ollama', 'openai', 'auto'.
    Raises ValueError for any unrecognized backend at boundary.
    """
    if backend is None:
        b = os.environ.get("CONSCIO_EMBED_BACKEND", "").strip().lower()
    else:
        b = backend.strip().lower()
    if not b:
        return "native"
    if b not in VALID_BACKENDS:
        raise ValueError(
            f"Unknown CONSCIO_EMBED_BACKEND: {b!r}. Valid options: {list(VALID_BACKENDS)}"
        )
    return b


def _resolve_model() -> tuple[str, int]:
    """Resolve model name + dimension from env vars or defaults."""
    model = os.environ.get("CONSCIO_EMBED_MODEL", DEFAULT_MODEL)
    dim = int(os.environ.get("CONSCIO_EMBED_DIM", "0"))
    if dim == 0:
        dim = LARGE_DIMENSION if model == LARGE_MODEL else DEFAULT_DIMENSION
    return model, dim


class EmbeddingProvider:
    """Unified embedder with native-first policy.

    Modes via CONSCIO_EMBED_BACKEND:
    - native (default/unset): sentence_transformers in-process only (all-MiniLM-L6-v2),
      ZERO network probes to Ollama/LM Studio.
    - ollama: Ollama local daemon only.
    - openai: OpenAI-compatible API (LM Studio local) only.
    - auto: legacy fallback chain (Ollama -> OpenAI -> sentence_transformers)
      with explicit WARNING in logs.
    """

    def __init__(self, force_no_network: bool = False, backend: str | None = None):
        model_name, dim = _resolve_model()
        self.model_name = model_name
        self.default_dimension = dim
        self.backend = resolve_embed_backend(backend)
        self.active_backend: str | None = None
        self._force_no_network = force_no_network
        self._embedder = None  # injected by tests or probed on first use
        # v3.6: whether the embedder has already been probed once. Without
        # this, a failed probe was silently retried on EVERY embed() call.
        self._probed = False

    def get_signature(self) -> dict[str, str | int]:
        """Return the vector-space signature for this provider."""
        return {
            "backend": self.active_backend or self.backend,
            "model": self.model_name,
            "dimension": self.default_dimension,
            "version": "1.0",
        }

    def get_embedder(self):
        """Probe available embedder lazily, at most once. Returns None if
        none available (cached — a failed probe is not retried on later
        calls within this instance's lifetime)."""
        if self._force_no_network:
            return None
        if self._embedder is not None:
            return self._embedder
        if self._probed:
            return None
        self._probed = True

        if self.backend == "native":
            # sentence_transformers (NATIVE, no daemon, no network probe)
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(self.model_name)
                v = model.encode("test")
                if hasattr(v, "tolist"):
                    v = v.tolist()
                elif hasattr(v, "__iter__"):
                    v = list(v)
                if v and len(v) == self.default_dimension:
                    self._embedder = model
                    self.active_backend = "native"
                    return model
            except ImportError:
                logger.debug("sentence_transformers not installed — skipping")
            except Exception as e:
                logger.debug(f"sentence_transformers failed: {e}")
            return None

        if self.backend == "ollama":
            # Explicit opt-in Ollama
            try:
                from .session_rag import OllamaEmbedder
                ed = OllamaEmbedder()
                v = ed.embed("test")
                if v and len(v) == self.default_dimension:
                    self._embedder = ed
                    self.active_backend = "ollama"
                    return ed
            except Exception as e:
                logger.debug(f"OllamaEmbedder unavailable: {e}")
            return None

        if self.backend == "openai":
            # Explicit opt-in OpenAI / LM Studio
            try:
                from .session_rag import OpenAICompatibleEmbedder
                ed = OpenAICompatibleEmbedder()
                v = ed.embed("test")
                if v and len(v) == self.default_dimension:
                    self._embedder = ed
                    self.active_backend = "openai"
                    return ed
            except Exception as e:
                logger.debug(f"OpenAICompatibleEmbedder unavailable: {e}")
            return None

        if self.backend == "auto":
            # Legacy fallback chain with explicit deprecation WARNING
            # 1. Try Ollama
            try:
                from .session_rag import OllamaEmbedder
                ed = OllamaEmbedder()
                v = ed.embed("test")
                if v and len(v) == self.default_dimension:
                    self._embedder = ed
                    self.active_backend = "ollama"
                    logger.warning(
                        "CONSCIO_EMBED_BACKEND=auto is a deprecated fallback mode; "
                        "selected backend: ollama"
                    )
                    return ed
            except Exception as e:
                logger.debug(f"OllamaEmbedder unavailable: {e}")

            # 2. Try OpenAI compatible (LM Studio)
            try:
                from .session_rag import OpenAICompatibleEmbedder
                ed = OpenAICompatibleEmbedder()
                v = ed.embed("test")
                if v and len(v) == self.default_dimension:
                    self._embedder = ed
                    self.active_backend = "openai"
                    logger.warning(
                        "CONSCIO_EMBED_BACKEND=auto is a deprecated fallback mode; "
                        "selected backend: openai"
                    )
                    return ed
            except Exception as e:
                logger.debug(f"OpenAICompatibleEmbedder unavailable: {e}")

            # 3. Try sentence_transformers (NATIVE)
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(self.model_name)
                v = model.encode("test")
                if hasattr(v, "tolist"):
                    v = v.tolist()
                elif hasattr(v, "__iter__"):
                    v = list(v)
                if v and len(v) == self.default_dimension:
                    self._embedder = model
                    self.active_backend = "native"
                    logger.warning(
                        "CONSCIO_EMBED_BACKEND=auto is a deprecated fallback mode; "
                        "selected backend: native"
                    )
                    return model
            except ImportError:
                logger.debug("sentence_transformers not installed — skipping")
            except Exception as e:
                logger.debug(f"sentence_transformers failed: {e}")

            logger.warning(
                "CONSCIO_EMBED_BACKEND=auto is a deprecated fallback mode; "
                "no backend available"
            )
            return None

        return None

    def available(self) -> bool:
        """Return True if any embedder is available."""
        return self.get_embedder() is not None

    def embed(self, text: str) -> list[float] | None:
        """Embed text. Returns None if no embedder available."""
        ed = self.get_embedder()
        if ed is None:
            return None
        try:
            if hasattr(ed, "encode") and not hasattr(ed, "embed"):
                v = ed.encode(text)  # type: ignore[union-attr]  # type: ignore[union-attr]
                return list(v) if v is not None else None  # type: ignore[arg-type]
            v = ed.embed(text)  # type: ignore[union-attr]
            return list(v) if v is not None else None
        except Exception as e:
            logger.warning(f"embed failed: {e}")
            return None

    def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Batch embed. Returns None if no embedder available."""
        ed = self.get_embedder()
        if ed is None:
            return None
        try:
            if hasattr(ed, "encode") and not hasattr(ed, "embed"):
                vecs = ed.encode(texts)  # type: ignore[union-attr]
                return [list(v) for v in vecs] if vecs is not None else None  # type: ignore[arg-type]
            if hasattr(ed, "embed_batch"):
                vecs = ed.embed_batch(texts)  # type: ignore[union-attr]
                return [list(v) for v in vecs] if vecs else None  # type: ignore[arg-type]
            return [list(ed.embed(t)) for t in texts]  # type: ignore[union-attr]
        except Exception as e:
            logger.warning(f"embed_batch failed: {e}")
            return None
