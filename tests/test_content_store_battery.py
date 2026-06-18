# tests/test_content_store_battery.py
"""v1.9 deep battery — ContentStore adversarial edge cases beyond the
prior-class sweep.

B-009: _chunk_content with chunk_size<=0 spins forever (remaining never shrinks
       → infinite loop, unbounded list growth). Latent like B-004 (no caller
       passes it today) but a HANG is strictly worse than B-004's over-fetch, so
       it gets the same treatment: a guard + try_break.

Invariant lock: _rrf_merge fetches BOTH porter- and trigram-originated rowids
       from the single `chunks` table. That is only correct because every chunk
       is inserted into `chunks` and `chunks_trigram` in lockstep and deleted in
       lockstep, so a rowid means the same chunk in both. A trigram-only hit
       (substring not a porter token), found AFTER a delete punches a rowid gap,
       proves the alignment still holds.
"""
import signal

import pytest

from conscio.content_store import ContentStore


@pytest.fixture
def store(tmp_path):
    s = ContentStore(db_path=tmp_path / "battery.db")
    yield s
    s.close()


class _Timeout(Exception):
    pass


def _run_with_timeout(fn, seconds: float = 2.0):
    """Run fn() but raise _Timeout if it hasn't returned in `seconds`.

    SIGALRM fires in the main thread (pytest's thread) between bytecodes, so it
    interrupts a pure-Python infinite loop cleanly and frees its locals — no
    leaked daemon thread, no unbounded list eating RAM after the test.
    """
    def _handler(signum, frame):
        raise _Timeout()

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# ── B-009: chunk_size<=0 must not hang ────────────────────────────────────────
class TestChunkSizeGuard:
    def test_try_break_zero_chunk_size_terminates(self, store):
        try:
            chunks = _run_with_timeout(
                lambda: store._chunk_content("hello world " * 5, 0))
        except _Timeout:
            pytest.fail("_chunk_content(chunk_size=0) hung — infinite loop")
        assert "".join(chunks).strip()              # produced real content

    def test_try_break_negative_chunk_size_terminates(self, store):
        try:
            chunks = _run_with_timeout(
                lambda: store._chunk_content("alpha bravo charlie delta", -10))
        except _Timeout:
            pytest.fail("_chunk_content(chunk_size<0) hung — infinite loop")
        assert "".join(chunks).strip()

    def test_try_break_index_zero_chunk_size_terminates(self, store):
        # the public entry point (index) must not hang either
        try:
            sid = _run_with_timeout(
                lambda: store.index("z", "some real content here", "reflection",
                                    chunk_size=0))
        except _Timeout:
            pytest.fail("index(chunk_size=0) hung — infinite loop")
        assert sid > 0
        assert store.get_source(sid).chunk_count >= 1

    def test_try_keep_normal_chunk_size_preserves_content(self, store):
        content = "\n\n".join(f"unique paragraph token {i}" for i in range(20))
        chunks = store._chunk_content(content, 200)
        joined = "\n\n".join(chunks)
        for i in range(20):
            assert f"unique paragraph token {i}" in joined


# ── Invariant lock: RRF cross-table rowid alignment survives a delete ─────────
class TestRRFRowidAlignment:
    def test_try_keep_trigram_hit_maps_to_right_chunk_after_delete(self, store):
        a = store.index("a", "alpha apple orchard", "reflection")
        store.index("b", "bravo banana zzz51155zzz marker", "error",
                    content_type="log")
        store.index("c", "charlie cherry tree", "reflection")

        store.delete_source(a)                      # punch a rowid gap in BOTH

        # "51155" is a substring inside a single porter token (zzz51155zzz), so
        # ONLY the trigram index can find it. Its rowid is resolved against the
        # `chunks` table in _rrf_merge — must still be the bravo chunk.
        results = store.search("51155")
        assert results, "trigram substring hit lost after delete"
        assert any("zzz51155zzz" in r.content for r in results), (
            "trigram rowid mis-mapped to the wrong chunk after a rowid gap")
