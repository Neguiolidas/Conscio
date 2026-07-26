"""TDD: VectorBackend — cosine search in SQLite BLOB."""
import math
import random

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


# ─── Vectorized scoring (C1) ────────────────────────────────────────────

def _cosine_ref(a: list[float], b: list[float]) -> float:
    """Naive reference cosine — the pre-fix per-row Python implementation."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def test_vectorized_search_matches_naive_cosine(tmp_path):
    """The batched numpy scoring must return exactly the naive ranking.

    C1 replaced a per-row Python loop with a single matrix-vector product;
    this pins the numerical result so the optimization can't silently change
    which chunks recall() sees.
    """
    rng = random.Random(1234)
    dim = 32
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=dim)
    vecs = {}
    for i in range(120):
        v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
        vecs[f"doc{i}"] = v
        vb.add(f"doc{i}", v)

    query = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    got = vb.search(query, limit=10)

    expected = sorted(
        ((vid, _cosine_ref(query, v)) for vid, v in vecs.items()),
        key=lambda kv: -kv[1],
    )[:10]

    assert [r["id"] for r in got] == [vid for vid, _ in expected]
    for r, (_, score) in zip(got, expected):
        assert abs(r["score"] - score) < 1e-5


def test_search_category_prefilter_restricts_candidates(tmp_path):
    """A category-scoped search must only ever return that category.

    Pre-filtering in SQL (rather than full-scan-then-filter) is what keeps a
    scoped recall from paying for the whole corpus; the observable contract is
    that no foreign-category row can leak into the results even when it is a
    much better cosine match.
    """
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("ref1", [1.0, 0.0], category="reference")
    vb.add("ref2", [0.9, 0.1], category="reference")
    vb.add("refl1", [1.0, 0.0], category="reflection")

    scoped = vb.search([1.0, 0.0], limit=10, category="reference")
    assert {r["id"] for r in scoped} == {"ref1", "ref2"}

    unscoped = vb.search([1.0, 0.0], limit=10)
    assert {r["id"] for r in unscoped} == {"ref1", "ref2", "refl1"}


def test_search_category_prefilter_unknown_category_is_empty(tmp_path):
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("a", [1.0, 0.0], category="reference")
    assert vb.search([1.0, 0.0], limit=5, category="nope") == []


def test_rows_without_category_are_still_searchable_unscoped(tmp_path):
    """Vectors written before the category column existed must not vanish."""
    vb = VectorBackend(db_path=tmp_path / "vec.db", dimension=2)
    vb.add("legacy", [1.0, 0.0])  # no category
    results = vb.search([1.0, 0.0], limit=5)
    assert [r["id"] for r in results] == ["legacy"]
