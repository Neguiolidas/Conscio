"""TDD: Dedup — hash + similarity."""
import pytest

from conscio.dedup import Deduplicator


def test_dedup_exact_match(tmp_path):
    dd = Deduplicator(db_path=tmp_path / "dd.db")
    h1 = dd.compute_hash("conteudo identico")
    h2 = dd.compute_hash("conteudo identico")
    assert h1 == h2
    # Hash without register is not duplicate
    assert not dd.is_duplicate(h1)
    # After register, it is
    dd.register(h1, "conteudo identico")
    assert dd.is_duplicate(h1)
    assert dd.is_duplicate(h2)  # same hash


def test_dedup_different(tmp_path):
    dd = Deduplicator(db_path=tmp_path / "dd.db")
    h = dd.compute_hash("conteudo diferente")
    assert not dd.is_duplicate(h)


def test_dedup_near_match(tmp_path):
    """Similar beyond threshold flags near-duplicate."""
    dd = Deduplicator(db_path=tmp_path / "dd.db", similarity_threshold=0.85)
    text1 = "O Conscio framework de consciencia para agentes"
    text2 = "O Conscio framework de consciencia para agentes LLM"
    assert dd.is_near_duplicate(text1, text2)


def test_dedup_far_match(tmp_path):
    """Dissimilar texts are not near-duplicate."""
    dd = Deduplicator(db_path=tmp_path / "dd.db", similarity_threshold=0.85)
    a = "Lorem ipsum dolor sit amet"
    b = "Python script with classes and functions"
    assert not dd.is_near_duplicate(a, b)


def test_dedup_register(tmp_path):
    dd = Deduplicator(db_path=tmp_path / "dd.db")
    h = dd.compute_hash("texto original")
    dd.register(h, "texto original")
    assert dd.is_duplicate(h)


def test_dedup_stats(tmp_path):
    dd = Deduplicator(db_path=tmp_path / "dd.db")
    dd.register("h1", "a")
    dd.register("h2", "b")
    assert dd.stats()["total"] == 2


def test_dedup_unicode_normalization(tmp_path):
    """Acentos e case não devem diferenciar hashes."""
    dd = Deduplicator(db_path=tmp_path / "dd.db")
    # Same content, different capitalization/accents
    h1 = dd.compute_hash("São Paulo")
    h2 = dd.compute_hash("sao paulo")
    assert h1 == h2


def test_dump_closes_the_destination_when_the_copy_fails(tmp_path, monkeypatch):
    """v3.9.4: same unguarded `backup()` as Hallways.dump, and the same leaked
    destination handle when it raises."""
    import sqlite3

    dd = Deduplicator(db_path=tmp_path / "dd.db")
    closed: list[str] = []
    real_connect = sqlite3.connect

    class _Tracked:
        def __init__(self, conn):
            self._conn = conn

        def close(self):
            closed.append("dst")
            self._conn.close()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: _Tracked(real_connect(*a, **k)))

    def _explode():
        raise RuntimeError("backup source unavailable")

    monkeypatch.setattr(dd, "_conn_get", _explode)

    with pytest.raises(RuntimeError):
        dd.dump(tmp_path / "copy.db")

    assert closed == ["dst"]
    monkeypatch.undo()
    dd.close()
