"""
session_rag.py — RAG layer over session DB.

Chunking + embedding + semantic search over conversation history.
Supports any OpenAI-compatible embedding endpoint (LM Studio, vLLM,
llama.cpp server, Ollama with OpenAI mode, etc.) plus native Ollama API.
Zero external deps beyond numpy + sqlite3.

Architecture:
    SessionDB → chunker → embedder → SessionVectorStore → semantic_search()

Coexists with native session_search (FTS5) — this adds semantic
retrieval on top. Conscio auto-improves chunking/embedding params.

Usage:
  from session_rag import SessionRAG
  rag = SessionRAG()
  rag.index_recent_sessions()  # chunk + embed latest sessions
  results = rag.search("como resolver o bug do OutputFilter")
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
SESSION_DB = HERMES_HOME / "state.db"
RAG_DB = HERMES_HOME / "consciousness" / "session_rag.db"

# Default endpoints — auto-detected by session_rag_factory.py
# OpenAI-compatible (LM Studio, vLLM, llama.cpp server, etc.)
DEFAULT_EMBED_URL = "http://127.0.0.1:1234/v1/embeddings"
DEFAULT_EMBED_MODEL = "text-embedding-nomic-embed-text-v1.5"
# Native Ollama API (legacy fallback)
OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embeddings"
OLLAMA_EMBED_MODEL = "nomic-embed-text"
EMBEDDING_DIM = 768

# Chunking params
CHUNK_MAX_TOKENS = 256  # ~1024 chars (chars/4 = tokens)
CHUNK_OVERLAP_CHARS = 100
CHUNK_MIN_CHARS = 50

# Skip patterns
SKIP_PREFIXES = [
    "[CONTEXT COMPACTION",
    "[Your active task",
    "[IMPORTANT: Background",
    "[System note:",
    "[IMPORTANT: You are running as a scheduled cron",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    id: str = ""
    session_id: str = ""
    role: str = ""
    content: str = ""
    embedding: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass
class SearchResult:
    chunk_id: str = ""
    session_id: str = ""
    role: str = ""
    content: str = ""
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Chunker — splits messages into semantically meaningful chunks
# ---------------------------------------------------------------------------

class SessionChunker:
    """Splits session messages into overlapping chunks with metadata."""

    def __init__(self, max_chars: int = CHUNK_MAX_TOKENS * 4,
                 overlap: int = CHUNK_OVERLAP_CHARS,
                 min_chars: int = CHUNK_MIN_CHARS):
        self.max_chars = max_chars
        self.overlap = overlap
        self.min_chars = min_chars

    def is_noise(self, content: str) -> bool:
        return any(content.startswith(p) for p in SKIP_PREFIXES)

    def chunk_message(self, session_id: str, role: str, content: str,
                      msg_id: int = 0) -> list[Chunk]:
        """Split a single message into one or more chunks."""
        if self.is_noise(content) or len(content) < self.min_chars:
            return []

        # Strip noise patterns
        for pattern in [
            r"\[CONTEXT COMPACTION[^\]]*\]",
            r"\[Your active task list was preserved[^\]]*\]",
        ]:
            content = re.sub(pattern, "", content, flags=re.DOTALL).strip()

        if not content or len(content) < self.min_chars:
            return []

        chunks = []
        if len(content) <= self.max_chars:
            # Single chunk
            chunk_id = hashlib.sha256(
                f"{session_id}:{msg_id}:0".encode()
            ).hexdigest()[:16]
            chunks.append(Chunk(
                id=chunk_id,
                session_id=session_id,
                role=role,
                content=content,
                metadata={"msg_id": msg_id, "chunk_idx": 0},
            ))
        else:
            # Overlapping chunks
            start = 0
            idx = 0
            while start < len(content):
                end = min(start + self.max_chars, len(content))
                # Try to break at sentence boundary
                if end < len(content):
                    last_period = content.rfind(".", start, end)
                    last_newline = content.rfind("\n", start, end)
                    break_point = max(last_period, last_newline)
                    if break_point > start + self.min_chars:
                        end = break_point + 1

                chunk_content = content[start:end].strip()
                if chunk_content and len(chunk_content) >= self.min_chars:
                    chunk_id = hashlib.sha256(
                        f"{session_id}:{msg_id}:{idx}".encode()
                    ).hexdigest()[:16]
                    chunks.append(Chunk(
                        id=chunk_id,
                        session_id=session_id,
                        role=role,
                        content=chunk_content,
                        metadata={"msg_id": msg_id, "chunk_idx": idx},
                    ))

                next_start = end - self.overlap
                # Guard: ensure progress — if overlap would send us backward or stall, advance
                if next_start <= start:
                    next_start = end
                if next_start >= len(content):
                    break
                start = next_start
                idx += 1

        return chunks

    def chunk_session(self, session_id: str,
                      messages: list[dict]) -> list[Chunk]:
        """Chunk all messages in a session."""
        all_chunks = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role not in ("user", "assistant"):
                continue
            chunks = self.chunk_message(session_id, role, content, msg_id=i)
            all_chunks.extend(chunks)
        return all_chunks


# ---------------------------------------------------------------------------
# Embedder — generates embeddings via OpenAI-compatible or Ollama API
# ---------------------------------------------------------------------------

class OpenAICompatibleEmbedder:
    """Generate embeddings using any OpenAI-compatible endpoint.

    Works with LM Studio, vLLM, llama.cpp server, Ollama (OpenAI mode),
    and any server that implements POST /v1/embeddings with the OpenAI schema.
    """

    def __init__(self, model: str = DEFAULT_EMBED_MODEL,
                 url: str = DEFAULT_EMBED_URL,
                 api_key: str = "",
                 dim: int = EMBEDDING_DIM,
                 batch_size: int = 8):
        self.model = model
        self.url = url
        self.api_key = api_key
        self.dim = dim
        self.batch_size = batch_size

    def embed(self, text: str) -> list[float]:
        """Get embedding for a single text (OpenAI format)."""
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.model,
            "input": text[:4000],  # Truncate long texts
        }).encode("utf-8")

        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                # OpenAI format: {"data": [{"embedding": [...]}]}
                embeddings = data.get("data", [])
                if embeddings and isinstance(embeddings[0], dict):
                    return embeddings[0].get("embedding", [])
                return []
        except (urllib.error.URLError, OSError) as e:
            logger.warning(f"Embedding failed: {e}")
            return []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts (sequential — most local servers don't batch)."""
        return [self.embed(t) for t in texts]


class OllamaEmbedder:
    """Generate embeddings using native Ollama API (legacy).

    Uses the Ollama-specific format: {"model": ..., "prompt": ...}.
    For Ollama's OpenAI-compatible mode, use OpenAICompatibleEmbedder instead.
    """

    def __init__(self, model: str = OLLAMA_EMBED_MODEL,
                 url: str = OLLAMA_EMBED_URL,
                 dim: int = EMBEDDING_DIM,
                 batch_size: int = 8):
        self.model = model
        self.url = url
        self.dim = dim
        self.batch_size = batch_size

    def embed(self, text: str) -> list[float]:
        """Get embedding for a single text (Ollama format)."""
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.model,
            "prompt": text[:2000],  # Truncate long texts
        }).encode("utf-8")

        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("embedding", [])
        except (urllib.error.URLError, OSError) as e:
            logger.warning(f"Embedding failed: {e}")
            return []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts (sequential — Ollama doesn't batch)."""
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# Vector Store — SQLite-backed with numpy cosine similarity
# ---------------------------------------------------------------------------

class SessionVectorStore:
    """SQLite-backed vector store for session chunks.

    Why not FAISS/ChromaDB? Because we want:
    - Zero external deps beyond numpy (already installed)
    - WAL mode for concurrent reads
    - Full control over schema and queries
    - Coexistence with native session DB
    """

    def __init__(self, db_path: Path = RAG_DB, dim: int = EMBEDDING_DIM,
                 embed_model: Optional[str] = None):
        self.db_path = db_path
        self.dim = dim
        self.embed_model = embed_model
        # True if a backend change cleared embeddings and a re-index is needed.
        self.reindex_required = False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if embed_model is not None:
            self._sync_embedder_identity(embed_model, dim)

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB,
                msg_id INTEGER,
                chunk_idx INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_session
            ON chunks(session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_created
            ON chunks(created_at)
        """)
        conn.commit()
        conn.close()

    def _sync_embedder_identity(self, model: str, dim: int):
        """Detect a changed embedding backend and force a clean re-index.

        Vectors from different embedding models (or dims) are incomparable; mixing
        them silently corrupts cosine search. We persist the (model, dim) the store
        was built with; if the configured embedder differs, we drop the now-stale
        embeddings (keeping chunk text) so the indexer rebuilds them. First build
        just records the identity.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = dict(conn.execute(
                "SELECT key, value FROM meta WHERE key IN ('embed_model', 'embed_dim')"
            ).fetchall())
            stored_model = rows.get("embed_model")
            stored_dim = int(rows["embed_dim"]) if rows.get("embed_dim") else None
            if stored_model is not None and (stored_model != model or stored_dim != dim):
                logger.warning(
                    "Embedding backend changed (%s/%s -> %s/%s); clearing embeddings "
                    "for re-index", stored_model, stored_dim, model, dim,
                )
                conn.execute("UPDATE chunks SET embedding = NULL "
                             "WHERE embedding IS NOT NULL")
                self.reindex_required = True
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                         ("embed_model", model))
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                         ("embed_dim", str(dim)))
            conn.commit()
        finally:
            conn.close()

    def _emb_blob(self, embedding) -> Optional[bytes]:
        """Pack an embedding to a float32 blob, dropping wrong-dim vectors.

        A vector whose length != the store's dim is incomparable with the rest of
        the store; storing it would corrupt cosine search. We drop it (store the
        chunk text with a NULL embedding) and warn, rather than poison the store.
        """
        if not embedding:
            return None
        if len(embedding) != self.dim:
            logger.warning("Dropping embedding: dim %d != store dim %d",
                           len(embedding), self.dim)
            return None
        return np.array(embedding, dtype=np.float32).tobytes()

    def upsert_chunk(self, chunk: Chunk):
        """Insert or update a chunk with its embedding."""
        emb_blob = self._emb_blob(chunk.embedding)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT OR REPLACE INTO chunks (id, session_id, role, content,
                                           embedding, msg_id, chunk_idx, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chunk.id,
            chunk.session_id,
            chunk.role,
            chunk.content,
            emb_blob,
            chunk.metadata.get("msg_id", 0),
            chunk.metadata.get("chunk_idx", 0),
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        conn.close()

    def upsert_batch(self, chunks: list[Chunk]):
        """Batch insert chunks."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        for chunk in chunks:
            emb_blob = self._emb_blob(chunk.embedding)
            conn.execute("""
                INSERT OR REPLACE INTO chunks (id, session_id, role, content,
                                               embedding, msg_id, chunk_idx, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chunk.id,
                chunk.session_id,
                chunk.role,
                chunk.content,
                emb_blob,
                chunk.metadata.get("msg_id", 0),
                chunk.metadata.get("chunk_idx", 0),
                datetime.now(timezone.utc).isoformat(),
            ))
        conn.commit()
        conn.close()

    def search(self, query_embedding: list[float], limit: int = 5,
               session_filter: Optional[str] = None,
               role_filter: Optional[str] = None,
               min_score: float = 0.3) -> list[SearchResult]:
        """Search by cosine similarity.

        Loads all embeddings from SQLite and computes similarity in-memory.
        This is efficient for <100k chunks (which covers years of conversation).
        """
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        where_clauses = ["embedding IS NOT NULL"]
        params: list = []
        if session_filter:
            where_clauses.append("session_id = ?")
            params.append(session_filter)
        if role_filter:
            where_clauses.append("role = ?")
            params.append(role_filter)

        where = " AND ".join(where_clauses)
        rows = conn.execute(
            f"SELECT id, session_id, role, content, embedding FROM chunks WHERE {where}",
            params,
        ).fetchall()
        conn.close()

        # Compute cosine similarity
        results = []
        for row in rows:
            chunk_id, session_id, role, content, emb_blob = row
            if not emb_blob:
                continue
            try:
                chunk_vec = np.frombuffer(emb_blob, dtype=np.float32)
            except ValueError:
                # Corrupted embedding blob (wrong length) - skip this chunk
                continue
            if chunk_vec.shape[0] != query_vec.shape[0]:
                # Dimension mismatch (e.g. residual vectors from a previous
                # embedding model) — skip so np.dot can never raise.
                continue
            chunk_norm = np.linalg.norm(chunk_vec)
            if chunk_norm == 0:
                continue
            score = float(np.dot(query_vec, chunk_vec) / (query_norm * chunk_norm))
            if score >= min_score:
                results.append(SearchResult(
                    chunk_id=chunk_id,
                    session_id=session_id,
                    role=role,
                    content=content,
                    score=score,
                ))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def get_stats(self) -> dict:
        """Get store statistics."""
        conn = sqlite3.connect(str(self.db_path))
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        with_emb = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM chunks"
        ).fetchone()[0]
        conn.close()

        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "total_chunks": total,
            "chunks_with_embeddings": with_emb,
            "sessions_indexed": sessions,
            "db_size_kb": round(db_size / 1024, 1),
        }

    def delete_session(self, session_id: str):
        """Remove all chunks for a session."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM chunks WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Main RAG interface
# ---------------------------------------------------------------------------

class SessionRAG:
    """High-level RAG interface over session DB.

    Usage:
        rag = SessionRAG()
        rag.index_recent_sessions(n=5)
        results = rag.search("como resolver o bug do OutputFilter")
    """

    def __init__(self, session_db: Path = SESSION_DB,
                 rag_db: Path = RAG_DB,
                 embedder=None,
                 chunker: Optional["SessionChunker"] = None):
        self.session_db = session_db
        self.chunker = chunker or SessionChunker()
        self.embedder = embedder or OpenAICompatibleEmbedder()
        # Bind the store's dimension/identity to the actual embedder so a backend
        # change triggers a clean re-index instead of silently corrupting search.
        self.store = SessionVectorStore(
            rag_db,
            dim=getattr(self.embedder, "dim", EMBEDDING_DIM),
            embed_model=getattr(self.embedder, "model", None),
        )

    def available(self) -> bool:
        """
        Probe whether semantic embedding is reachable (Ollama up).

        Returns True if the embedder returns a non-empty vector for a tiny
        prompt. Cheap and safe — used by the engine to decide whether to use
        SessionRAG or fall back to ContentStore FTS5.
        """
        try:
            return bool(self.embedder.embed("ping"))
        except Exception:
            return False

    def _get_sessions(self, n: int = 5, source_filter: str = "telegram",
                      min_messages: int = 2) -> list[dict]:
        """Get recent sessions from session DB."""
        if not self.session_db.exists():
            return []

        conn = sqlite3.connect(str(self.session_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT id, source, model, started_at, message_count, title
            FROM sessions
            WHERE source = ? AND message_count >= ?
            ORDER BY started_at DESC
            LIMIT ?
        """, (source_filter, min_messages, n))

        sessions = [dict(r) for r in cur.fetchall()]
        conn.close()
        return sessions

    def _get_session_messages(self, session_id: str) -> list[dict]:
        """Get messages for a session."""
        conn = sqlite3.connect(str(self.session_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("""
            SELECT id, role, content
            FROM messages
            WHERE session_id = ? AND role IN ('user', 'assistant')
            ORDER BY id ASC
        """, (session_id,))

        messages = [dict(r) for r in cur.fetchall()]
        conn.close()
        return messages

    def index_recent_sessions(self, n: int = 5,
                              source_filter: str = "telegram") -> dict:
        """Index the N most recent sessions into the RAG store.

        Returns stats about what was indexed.
        """
        sessions = self._get_sessions(n, source_filter)
        total_chunks = 0
        total_embedded = 0

        for session in sessions:
            sid = session["id"]
            messages = self._get_session_messages(sid)
            if not messages:
                continue

            # Delete old chunks for this session (re-index)
            self.store.delete_session(sid)

            # Chunk
            chunks = self.chunker.chunk_session(sid, messages)
            if not chunks:
                continue

            # Embed (batch)
            texts = [c.content for c in chunks]
            embeddings = self.embedder.embed_batch(texts)

            # Assign embeddings
            for chunk, emb in zip(chunks, embeddings):
                if emb:
                    chunk.embedding = emb
                    total_embedded += 1

            # Store
            self.store.upsert_batch(chunks)
            total_chunks += len(chunks)

            logger.info(
                f"Indexed session {sid[:20]}...: "
                f"{len(messages)} msgs → {len(chunks)} chunks "
                f"({total_embedded} embedded)"
            )

        stats = self.store.get_stats()
        stats["index_result"] = {
            "sessions_indexed": len(sessions),
            "new_chunks": total_chunks,
            "new_embedded": total_embedded,
        }
        return stats

    def search(self, query: str, limit: int = 5,
               session_filter: Optional[str] = None,
               role_filter: Optional[str] = None,
               min_score: float = 0.3) -> list[SearchResult]:
        """Semantic search over session history.

        Args:
            query: Natural language query
            limit: Max results
            session_filter: Only search within this session_id
            role_filter: Only 'user' or 'assistant' messages
            min_score: Minimum cosine similarity (0-1)
        """
        query_embedding = self.embedder.embed(query)
        if not query_embedding:
            logger.warning("Query embedding failed — no results")
            return []

        return self.store.search(
            query_embedding, limit=limit,
            session_filter=session_filter,
            role_filter=role_filter,
            min_score=min_score,
        )

    def search_and_format(self, query: str, limit: int = 5) -> str:
        """Search and return formatted results for injection into context."""
        results = self.search(query, limit=limit)
        if not results:
            return f"No RAG results for: {query}"

        lines = [f"## RAG Search: \"{query}\"", ""]
        for i, r in enumerate(results, 1):
            session_short = r.session_id[:20] + "..."
            lines.append(
                f"**{i}.** [{r.role}] (score={r.score:.2f}, "
                f"session={session_short})"
            )
            lines.append(f"   {r.content[:200]}")
            lines.append("")

        return "\n".join(lines)

    def get_stats(self) -> dict:
        """Get RAG store statistics."""
        return self.store.get_stats()
