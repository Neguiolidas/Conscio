"""
session_rag_factory.py — Shared factory for lazy SessionRAG construction.

Single place that handles:
  1. Deferred import of ``conscio.session_rag`` (avoids numpy/ollama at startup)
  2. ``.available()`` probe (Ollama up?)
  3. Graceful fallback to None on ImportError or any other failure

Used by both ConsciousnessEngine and ContentLayerManager so that the
init logic is defined exactly once.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def create_session_rag():
    """Create a SessionRAG instance if possible, else None.

    Returns a SessionRAG whose ``.available()`` probe succeeded, or None
    when the module is missing / Ollama is down / any error occurs.
    Safe to call as a provider callback (no arguments, no side-effects).
    """
    try:
        from .session_rag import SessionRAG
        rag = SessionRAG()
        return rag if rag.available() else None
    except ImportError:
        logger.debug("session_rag module unavailable — RAG provider disabled")
        return None
    except Exception:
        logger.debug("SessionRAG construction failed — RAG provider disabled",
                      exc_info=True)
        return None
