"""
session_rag_factory.py — Shared factory for lazy SessionRAG construction.

Single place that handles:
  1. Deferred import of ``conscio.session_rag`` (avoids numpy at startup)
  2. Auto-detect which embedding backend is available:
     a. OpenAI-compatible endpoint (LM Studio, vLLM, llama.cpp server, etc.)
     b. Native Ollama API (legacy fallback)
  3. ``.available()`` probe (embedder up?)
  4. Graceful fallback to None on ImportError or any other failure

Used by both ConsciousnessEngine and ContentLayerManager so that the
init logic is defined exactly once.
"""
from __future__ import annotations

import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Probe order: try these endpoints in order, use the first that responds.
# Each entry: (name, embed_url, model, is_openai_format)
_PROBE_ENDPOINTS = [
    # LM Studio (OpenAI-compatible)
    ("lm-studio", "http://127.0.0.1:1234/v1/embeddings",
     "text-embedding-nomic-embed-text-v1.5", True),
    # vLLM / llama.cpp server (OpenAI-compatible, common ports)
    ("vllm-8000", "http://127.0.0.1:8000/v1/embeddings", "", True),
    ("llamacpp-8080", "http://127.0.0.1:8080/v1/embeddings", "", True),
    # Ollama native API (legacy)
    ("ollama", "http://127.0.0.1:11434/api/embeddings",
     "nomic-embed-text", False),
]


def _probe_endpoint(url: str, timeout: float = 3.0) -> bool:
    """Check if an embedding endpoint is reachable (HEAD or tiny POST)."""
    try:
        req = urllib.request.Request(
            url,
            data=b'{"model":"probe","input":"hi"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Any 2xx means the endpoint is alive
            return 200 <= resp.status < 300
    except Exception:
        return False


def create_session_rag():
    """Create a SessionRAG instance if possible, else None.

    Auto-detects which embedding backend is available:
    1. Try OpenAI-compatible endpoints (LM Studio, vLLM, etc.)
    2. Fall back to native Ollama API
    3. Return None if nothing is reachable

    Returns a SessionRAG whose ``.available()`` probe succeeded, or None
    when the module is missing / no embedder is up / any error occurs.
    Safe to call as a provider callback (no arguments, no side-effects).
    """
    try:
        from .session_rag import (
            SessionRAG, OpenAICompatibleEmbedder, OllamaEmbedder,
        )
    except ImportError:
        logger.debug("session_rag module unavailable — RAG provider disabled")
        return None

    # Try each endpoint in order
    for name, url, model, is_openai in _PROBE_ENDPOINTS:
        if not _probe_endpoint(url):
            logger.debug(f"Embedding probe: {name} at {url} — not reachable")
            continue

        logger.info(f"Embedding probe: {name} at {url} — reachable")
        try:
            if is_openai:
                embedder = OpenAICompatibleEmbedder(
                    url=url, model=model or "text-embedding-nomic-embed-text-v1.5"
                )
            else:
                embedder = OllamaEmbedder(url=url, model=model or "nomic-embed-text")

            rag = SessionRAG(embedder=embedder)
            if rag.available():
                logger.info(f"SessionRAG ready with {name} embedder")
                return rag
            else:
                logger.debug(f"SessionRAG .available() failed with {name}")
        except Exception:
            logger.debug(f"SessionRAG construction failed with {name}",
                         exc_info=True)
            continue

    logger.debug("No embedding backend reachable — RAG provider disabled")
    return None
