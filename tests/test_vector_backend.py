"""TDD: VectorBackend — cosine search in SQLite BLOB."""
from conscio.vector_backend import VectorBackend


def test_vector_store_add_and_search(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=4)
    vb.add("doc1", [1.0, 0.0, 0.0, 0.0])
    vb.add("doc2", [0.0, 1.0, 0.0, 0.0])
    vb.add("doc3", [1.0, 0.1, 0.0, 0.0])
    results = vb.search([1.0, 0.0, 0.0, 0.0], limit=2)
    assert len(results) == 2
    assert results[0]["id"] == "doc1"
    assert results[0]["score"] > 0.99


def test_vector_store_dimension_mismatch(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=4)
    try:
        vb.add("bad", [1.0, 0.0])
        assert False
    except ValueError:
        pass


def test_vector_store_empty(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=4)
    assert vb.search([1.0, 0.0, 0.0, 0.0], limit=5) == []


def test_vector_store_persistence(tmp_path):
    db = tmp_path / "vec.db"
    vb = VectorBackend(db_path=db, dimension=4)
    vb.add("doc1", [1.0, 0.0, 0.0, 0.0])
    vb.close()
    vb2 = VectorBackend(db_path=db, dimension=4)
    results = vb2.search([1.0, 0.0, 0.0, 0.0], limit=1)
    assert len(results) == 1
    assert results[0]["id"] == "doc1"


def test_vector_store_score_ordering(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("a", [1.0, 0.0])
    vb.add("b", [0.7, 0.7])
    vb.add("c", [0.0, 1.0])
    results = vb.search([1.0, 0.0], limit=3)
    assert results[0]["id"] == "a"
    assert results[1]["id"] == "b"
    assert results[2]["id"] == "c"
    assert results[0]["score"] > results[1]["score"] > results[2]["score"]


def test_vector_store_nan_rejected(tmp_path):
    """Hostile review: NaN vector rejected."""
    import math
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=4)
    try:
        vb.add("nan", [float("nan"), 0.0, 0.0, 0.0])
        assert False, "should reject NaN"
    except ValueError:
        pass


def test_vector_store_large_dim(tmp_path):
    """Large dimension (10000) works."""
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=10000)
    vec = [0.0] * 10000
    vec[0] = 1.0
    vb.add("big", vec)
    results = vb.search(vec, limit=1)
    assert len(results) == 1
    assert results[0]["id"] == "big"


def test_vector_store_stats(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("a", [1.0, 0.0])
    vb.add("b", [0.0, 1.0])
    s = vb.stats()
    assert s["vectors"] == 2
    assert s["dimension"] == 2
    vb.close()
