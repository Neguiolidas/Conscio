"""TDD: sqlite-vec backend — cosine search via sqlite-vec extension.

Tests the SqliteVecBackend which uses sqlite-vec's vec0 virtual table
for C-native brute-force cosine search, replacing the Python/numpy O(n)
scan in VectorBackend.
"""

import pytest

from conscio.vector_backend import SqliteVecBackend, VectorBackend, has_sqlite_vec

# Skip all if sqlite-vec not installed
pytestmark = pytest.mark.skipif(
    not has_sqlite_vec(),
    reason="sqlite-vec not installed (pip install sqlite-vec)",
)


def test_sqlite_vec_available():
    """sqlite-vec extension loads in our SQLite."""
    assert has_sqlite_vec()


def test_sqlite_vec_store_add_and_search(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=4)
    vb.add("doc1", [1.0, 0.0, 0.0, 0.0])
    vb.add("doc2", [0.0, 1.0, 0.0, 0.0])
    vb.add("doc3", [1.0, 0.1, 0.0, 0.0])
    results = vb.search([1.0, 0.0, 0.0, 0.0], limit=2)
    assert len(results) == 2
    assert results[0]["id"] == "doc1"
    assert results[0]["score"] > 0.99


def test_sqlite_vec_dimension_mismatch(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=4)
    with pytest.raises(ValueError):
        vb.add("bad", [1.0, 0.0])


def test_sqlite_vec_empty(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=4)
    assert vb.search([1.0, 0.0, 0.0, 0.0], limit=5) == []


def test_sqlite_vec_persistence(tmp_path):
    db = tmp_path / "vec.db"
    vb = SqliteVecBackend(db_path=db, dimension=4)
    vb.add("doc1", [1.0, 0.0, 0.0, 0.0])
    vb.close()
    vb2 = SqliteVecBackend(db_path=db, dimension=4)
    results = vb2.search([1.0, 0.0, 0.0, 0.0], limit=1)
    assert len(results) == 1
    assert results[0]["id"] == "doc1"


def test_sqlite_vec_score_ordering(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("a", [1.0, 0.0])
    vb.add("b", [0.7, 0.7])
    vb.add("c", [0.0, 1.0])
    results = vb.search([1.0, 0.0], limit=3)
    assert results[0]["id"] == "a"
    assert results[1]["id"] == "b"
    assert results[2]["id"] == "c"
    # sqlite-vec returns distance (lower = better), we convert to score (higher = better)
    assert results[0]["score"] >= results[1]["score"] >= results[2]["score"]


def test_sqlite_vec_nan_rejected(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=4)
    with pytest.raises(ValueError):
        vb.add("nan", [float("nan"), 0.0, 0.0, 0.0])


def test_sqlite_vec_stats(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("a", [1.0, 0.0])
    vb.add("b", [0.0, 1.0])
    s = vb.stats()
    assert s["vectors"] == 2
    assert s["dimension"] == 2
    vb.close()


def test_sqlite_vec_category_filter(tmp_path):
    """Category-scoped search only returns that category."""
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("ref1", [1.0, 0.0], category="reference")
    vb.add("ref2", [0.9, 0.1], category="reference")
    vb.add("refl1", [1.0, 0.0], category="reflection")
    scoped = vb.search([1.0, 0.0], limit=10, category="reference")
    assert {r["id"] for r in scoped} == {"ref1", "ref2"}
    unscoped = vb.search([1.0, 0.0], limit=10)
    assert {r["id"] for r in unscoped} == {"ref1", "ref2", "refl1"}


def test_sqlite_vec_batch_add(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=2)
    items = [("a", [1.0, 0.0]), ("b", [0.0, 1.0]), ("c", [0.7, 0.7])]
    n = vb.add_batch(items)
    assert n == 3
    results = vb.search([1.0, 0.0], limit=3)
    assert len(results) == 3
    assert results[0]["id"] == "a"


def test_sqlite_vec_ensure_dimension(tmp_path):
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=4)
    assert vb.ensure_dimension(4) is True
    # Empty store: can adopt a new dimension
    assert vb.ensure_dimension(8) is True
    assert vb.dimension == 8
    # Now non-empty: can't change
    vb.add("test", [1.0] * 8)
    assert vb.ensure_dimension(16) is False
    assert vb.ensure_dimension(8) is True


def test_sqlite_vec_matches_naive_cosine(tmp_path):
    """Scores from sqlite-vec must match naive cosine ranking."""
    import random
    rng = random.Random(1234)
    dim = 32
    vb = SqliteVecBackend(db_path=tmp_path / "vec.db", dimension=dim)
    vecs = {}
    for i in range(120):
        v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
        vecs[f"doc{i}"] = v
        vb.add(f"doc{i}", v)
    query = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    got = vb.search(query, limit=10)
    # cosine: higher score = better match; verify ordering is correct
    scores = [r["score"] for r in got]
    assert scores == sorted(scores, reverse=True)
    # verify top result is what naive cosine would pick
    import math
    def _cosine_ref(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0: return 0.0
        return dot / (na * nb)
    expected_top = max(vecs.items(), key=lambda kv: _cosine_ref(query, kv[1]))
    assert got[0]["id"] == expected_top[0]


# ─── Factory: VectorBackend.with_engine() ──────────────────────────

def test_vector_backend_factory_sqlite_vec(tmp_path, monkeypatch):
    """VectorBackend can create a SqliteVecBackend via env var."""
    monkeypatch.setenv("CONSCIO_VEC_BACKEND", "sqlite_vec")
    vb = VectorBackend.with_engine(db_path=tmp_path / "vec.db", dimension=4)
    assert isinstance(vb, SqliteVecBackend)
    vb.add("test", [1.0, 0.0, 0.0, 0.0])
    results = vb.search([1.0, 0.0, 0.0, 0.0], limit=1)
    assert results[0]["id"] == "test"


def test_vector_backend_factory_default(tmp_path, monkeypatch):
    """Default factory returns the original VectorBackend."""
    monkeypatch.delenv("CONSCIO_VEC_BACKEND", raising=False)
    vb = VectorBackend.with_engine(db_path=tmp_path / "vec.db", dimension=4)
    assert type(vb) is VectorBackend  # exact type, not subclass


def test_vector_backend_factory_hnsw_nonexistent(tmp_path, monkeypatch):
    """HNSW backend is opt-in but gracefully degrades if hnswlib not installed."""
    import importlib.util
    monkeypatch.setenv("CONSCIO_VEC_BACKEND", "hnsw")
    if importlib.util.find_spec("hnswlib") is not None:
        pytest.skip("hnswlib is installed — skip degradation test")
    # Should fall back to default VectorBackend
    vb = VectorBackend.with_engine(db_path=tmp_path / "vec.db", dimension=4)
    assert type(vb) is VectorBackend
