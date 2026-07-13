"""Tests for SessionRAG — chunker, vector store, search (mocked embedder)."""
import numpy as np
import pytest

from conscio.session_rag import (
    SessionChunker, SessionVectorStore, SessionRAG, Chunk,
)


class FakeEmbedder:
    """Deterministic embedder: maps text → a fixed-dim vector, no network."""
    def __init__(self, dim=8, fail=False):
        self.dim = dim
        self.fail = fail

    def _vec(self, text):
        # Stable pseudo-embedding from char codes (not semantic, but deterministic).
        v = np.zeros(self.dim, dtype=np.float32)
        for i, ch in enumerate(text):
            v[i % self.dim] += (ord(ch) % 17)
        n = np.linalg.norm(v)
        return (v / n).tolist() if n else v.tolist()

    def embed(self, text):
        return [] if self.fail else self._vec(text)

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


# ── Chunker (pure, no deps) ──

def test_chunker_skips_noise():
    c = SessionChunker()
    assert c.chunk_message("s", "user", "[System note: ignore]") == []


def test_chunker_skips_too_short():
    c = SessionChunker(min_chars=50)
    assert c.chunk_message("s", "user", "hi") == []


def test_chunker_single_chunk_for_short_message():
    c = SessionChunker(max_chars=1000, min_chars=5)
    chunks = c.chunk_message("s", "user", "this is a normal length user message", msg_id=1)
    assert len(chunks) == 1
    assert chunks[0].session_id == "s"
    assert chunks[0].role == "user"


def test_chunker_splits_long_message():
    c = SessionChunker(max_chars=100, overlap=10, min_chars=5)
    text = "word " * 100  # 500 chars
    chunks = c.chunk_message("s", "assistant", text, msg_id=2)
    assert len(chunks) > 1


def test_chunk_session_filters_roles():
    c = SessionChunker(min_chars=5)
    msgs = [
        {"role": "system", "content": "system stuff here ignored"},
        {"role": "user", "content": "a real user question about bugs"},
    ]
    chunks = c.chunk_session("s", msgs)
    assert all(ch.role == "user" for ch in chunks)


# ── Vector store (numpy cosine) ──

@pytest.fixture
def store(tmp_path):
    return SessionVectorStore(tmp_path / "rag.db", dim=8)


def test_vector_store_upsert_and_search_ranks_by_similarity(store):
    emb = FakeEmbedder(dim=8)
    a = Chunk(id="a", session_id="s", role="user", content="apple banana",
              embedding=emb.embed("apple banana"))
    b = Chunk(id="b", session_id="s", role="user", content="zzz qqq",
              embedding=emb.embed("zzz qqq"))
    store.upsert_batch([a, b])
    results = store.search(emb.embed("apple banana"), limit=2, min_score=-1.0)
    assert results[0].chunk_id == "a"  # exact match ranks first


def test_vector_store_min_score_filters(store):
    emb = FakeEmbedder(dim=8)
    store.upsert_batch([Chunk(id="a", session_id="s", role="user",
                             content="apple", embedding=emb.embed("apple"))])
    # Query orthogonal-ish text with a high threshold → no results
    results = store.search(emb.embed("zzzzzz"), limit=5, min_score=0.99)
    assert results == []


def test_vector_store_delete_session(store):
    emb = FakeEmbedder(dim=8)
    store.upsert_batch([Chunk(id="a", session_id="s1", role="user",
                             content="x", embedding=emb.embed("x"))])
    store.upsert_batch([Chunk(id="b", session_id="s2", role="user",
                             content="y", embedding=emb.embed("y"))])
    store.delete_session("s1")
    assert store.get_stats()["total_chunks"] == 1


def test_vector_store_empty_query_returns_empty(store):
    assert store.search([0.0] * 8, limit=5) == []  # zero-norm query


# ── SessionRAG (injected embedder) ──

@pytest.fixture
def rag(tmp_path):
    return SessionRAG(
        session_db=tmp_path / "state.db",
        rag_db=tmp_path / "rag.db",
        embedder=FakeEmbedder(dim=8),
    )


def test_rag_available_true_with_working_embedder(rag):
    assert rag.available() is True


def test_rag_available_false_when_embedder_fails(tmp_path):
    r = SessionRAG(session_db=tmp_path / "s.db", rag_db=tmp_path / "r.db",
                   embedder=FakeEmbedder(fail=True))
    assert r.available() is False


def test_rag_search_returns_indexed_chunk(rag):
    # Manually store a chunk, then search semantically.
    emb = rag.embedder
    rag.store.upsert_batch([Chunk(
        id="c1", session_id="s", role="assistant",
        content="the OutputFilter bug was in the pipeline",
        embedding=emb.embed("the OutputFilter bug was in the pipeline"),
    )])
    results = rag.search("OutputFilter bug pipeline", limit=3, min_score=-1.0)
    assert results
    assert "OutputFilter" in results[0].content


def test_rag_search_empty_store(rag):
    assert rag.search("anything", min_score=-1.0) == []


# ── index_recent_sessions ──


def test_index_recent_sessions_returns_stats(tmp_path):
    # Create a session DB with some messages matching session schema
    import sqlite3
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            model TEXT,
            started_at TEXT,
            message_count INTEGER,
            title TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
    """)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
        ("s1", "telegram", "glm-5.1", "2024-01-01T00:00:00", 2, "test")
    )
    # Messages need to be long enough to pass min_chars=50
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s1", "user", "hello there how are you doing today this is a longer message to pass the minimum character threshold")
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s1", "assistant", "I am doing well thank you for asking this is also a sufficiently long response message")
    )
    conn.commit()
    conn.close()

    # Re-create RAG with the same DB
    rag2 = SessionRAG(
        session_db=tmp_path / "state.db",
        rag_db=tmp_path / "rag.db",
        embedder=FakeEmbedder(dim=8),
    )
    stats = rag2.index_recent_sessions(n=5, source_filter="telegram")
    assert "index_result" in stats
    assert stats["index_result"]["sessions_indexed"] == 1
    assert stats["index_result"]["new_chunks"] >= 1


def test_index_recent_sessions_empty_db(tmp_path):
    rag2 = SessionRAG(
        session_db=tmp_path / "state.db",
        rag_db=tmp_path / "rag.db",
        embedder=FakeEmbedder(dim=8),
    )
    stats = rag2.index_recent_sessions(n=5)
    assert stats["index_result"]["sessions_indexed"] == 0
    assert stats["index_result"]["new_chunks"] == 0


def test_index_recent_sessions_source_filter(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            model TEXT,
            started_at TEXT,
            message_count INTEGER,
            title TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        )
    """)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
        ("s2", "cron", "glm-5.1", "2024-01-01T00:00:00", 1, "cron job")
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        ("s2", "user", "cron job running this is a longer message")
    )
    conn.commit()
    conn.close()

    rag2 = SessionRAG(
        session_db=tmp_path / "state.db",
        rag_db=tmp_path / "rag.db",
        embedder=FakeEmbedder(dim=8),
    )
    stats = rag2.index_recent_sessions(n=10, source_filter="telegram")
    assert stats["index_result"]["sessions_indexed"] == 0  # cron filtered out


