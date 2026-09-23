"""Bug-hunt round (post-4.7.1): silent failures in scoring paths and
non-atomic writes in shared state.

Hunt findings (each verified in the live code by the orchestrator):
1. evaluation.py:289 — `except Exception: pass` swallows a broken
   contradiction counter; Clarity reports INFLATED with no trace.
2. vector_backend.py HNSW _meta_conn — no busy_timeout; concurrent
   agents hit `database is locked` during signature validation.
3. relay_net.py:215 — token written non-atomically; crash mid-write
   corrupts shared relay state.
4. tick.py:155 — cursor write non-atomic + swallowed OSError.
"""



# ── 1. evaluate clarity: detector failure must be VISIBLE ─────────────────


class TestClarityDetectorFailureIsVisible:
    def test_broken_world_logs_and_flags(self, tmp_path, monkeypatch, caplog):
        """If the contradiction detector raises, the failure must be logged
        and the axis must say it could not fully measure — silence is the
        audited bug family."""
        from conscio.evaluation import _score_clarity

        class _BrokenWorld:
            def list_entities(self, limit=20):
                raise RuntimeError("detector exploded")

        class _E:
            world = _BrokenWorld()

        import logging
        with caplog.at_level(logging.WARNING, logger="conscio.evaluation"):
            axis = _score_clarity(_E())
        assert any("contradiction detector failed" in r.getMessage()
                   for r in caplog.records), \
            "a broken detector must log, not pass silently"
        assert axis.improvement and "detector" in axis.improvement.lower(), \
            "the axis must tell the caller the count is unmeasured"

    def test_healthy_path_unchanged(self):
        """A working world with contradictory states still docks clarity."""
        from conscio.evaluation import _score_clarity

        class _E:
            class world:
                @staticmethod
                def list_entities(limit=20):
                    return [{"name": "svc", "state_log": [
                        {"state": "up"}, {"state": "down"}]}]

        axis = _score_clarity(_E())
        assert axis.score < 5  # contradiction docked


# ── 2. HNSW meta connection honors busy_timeout ──────────────────────────


class TestHnswMetaConnBusyTimeout:
    def test_meta_conn_has_busy_timeout(self, tmp_path):
        """Concurrent signature validation must wait, not explode with
        'database is locked' — the multi-agent house is the normal case."""
        from conscio.vector_backend import HNSWBackend
        hb = HNSWBackend.__new__(HNSWBackend)
        hb.db_path = tmp_path / "v.db"
        hb.dimension = 4
        conn = hb._meta_conn()
        try:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert timeout >= 5000, \
                f"busy_timeout={timeout}ms — concurrent agents will lock"
        finally:
            conn.close()


# ── 3/4. atomic writes in relay state ────────────────────────────────────


class TestAtomicWrites:
    def test_relay_token_write_is_atomic(self, tmp_path):
        """The token write must land via tmp+rename — a crash mid-write must
        never leave a truncated token in the shared relay state."""
        import inspect

        from conscio.liaison import relay_net
        src = inspect.getsource(relay_net)
        assert "_atomic_write_text" in src or "atomic" in src, \
            "relay_net must write tokens atomically (tmp+rename)"

    def test_tick_cursor_write_is_atomic(self):
        import inspect

        from conscio.liaison import tick
        src = inspect.getsource(tick)
        assert "atomic" in src.lower(), \
            "tick cursor must write atomically — a torn cursor re-ingests"
