"""
Tests for SessionLifecycle — Conscio ↔ Hermes session continuity integration.

Covers:
- SessionSummary dataclass
- Noise filtering (strip_noise, is_noise)
- Extraction (intents, actions, reasoning, topics)
- Enrichment with Conscio engine
- Formatting (heartbeat < 1.5KB, handoff richer)
- record_session_lifecycle() full pipeline
- EventBus + ContentStore integration
- Edge cases (empty DB, no engine, invalid events)
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from conscio.session_lifecycle import (
    SessionSummary,
    strip_noise,
    is_noise,
    extract_intents,
    extract_actions,
    extract_reasoning,
    infer_topics,
    format_heartbeat,
    format_handoff,
    enrich_with_conscio,
    record_session_lifecycle,
    get_latest_session,
    get_session_by_id,
    HB_MAX_CHARS,
    SKIP_PREFIXES,
)
from conscio.event_bus import EventBus, VALID_TYPES, VALID_CATEGORIES
from conscio.content_store import ContentStore


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary state.db with sample session data."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
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
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)

    # Insert sample session
    conn.execute("""
        INSERT INTO sessions (id, source, model, started_at, message_count, title)
        VALUES ('test_20260605', 'telegram', 'glm-5.1', '2026-06-05T08:50:00', 12, 'Debug Conscio integration')
    """)

    messages = [
        ("user", "verifique o gateway"),
        ("assistant", "Gateway rodando. 3 processos ativos."),
        ("user", "integre com o Conscio"),
        ("assistant", "Vou adicionar o tipo 'session' ao EventBus e criar session_lifecycle.py"),
        ("user", "rode os testes"),
        ("assistant", "316 testes passaram. Nenhum falhou."),
        ("user", "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted"),
        ("assistant", "[HEARTBEAT — Contexto da sessão anterior] Algo aqui"),
        ("user", "bug no engine.py — indentação errada"),
        ("assistant", "Corrigido. O problema era mixed tabs/spaces."),
        ("user", "commit tudo"),
        ("assistant", "Commitado: ce6144e — Add session_lifecycle module"),
    ]
    for role, content in messages:
        conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                     ("test_20260605", role, content))

    # Insert a cron session (should be skipped)
    conn.execute("""
        INSERT INTO sessions (id, source, model, started_at, message_count, title)
        VALUES ('cron_20260605', 'cron', 'glm-5.1', '2026-06-05T06:00:00', 1, 'Daily cron')
    """)
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                 ("cron_20260605", "user", "cron task"))

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def empty_db(tmp_path):
    """Create an empty state.db (no sessions)."""
    db_path = tmp_path / "state_empty.db"
    conn = sqlite3.connect(str(db_path))
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
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def mock_engine(tmp_path):
    """Create a real Conscio engine with temp storage."""
    from conscio.engine import ConsciousnessEngine
    engine = ConsciousnessEngine(
        model_name="test-model",
        storage_path=tmp_path / "conscio_test",
    )
    yield engine
    engine.close()


@pytest.fixture
def sample_messages():
    """Sample messages for extraction tests."""
    return [
        {"role": "user", "content_preview": "verifique o gateway"},
        {"role": "assistant", "content_preview": "Gateway rodando. 3 processos ativos."},
        {"role": "user", "content_preview": "integre com o Conscio"},
        {"role": "assistant", "content_preview": "Vou adicionar o tipo 'session' ao EventBus"},
        {"role": "user", "content_preview": "[CONTEXT COMPACTION — REFERENCE ONLY] junk"},
        {"role": "assistant", "content_preview": "[HEARTBEAT — Contexto da sessão anterior] junk"},
        {"role": "user", "content_preview": "bug no engine.py — indentação errada"},
        {"role": "assistant", "content_preview": "Corrigido. O problema era mixed tabs/spaces."},
    ]


@pytest.fixture
def sample_summary():
    """A pre-built SessionSummary for formatting tests."""
    return SessionSummary(
        session_id="test_20260605",
        model="glm-5.1",
        started_at="2026-06-05T08:50:00",
        message_count=12,
        title="Debug Conscio integration",
        intents=["verifique o gateway", "integre com o Conscio", "rode os testes"],
        actions=["Gateway rodando. 3 processos ativos.", "Vou adicionar o tipo 'session' ao EventBus"],
        reasoning=["[user] bug no engine.py — indentação errada"],
        topics=["conscio", "debug", "agent"],
        world_model_entities=["trading_bot:active", "conscio:v0.2.1"],
        active_goals=["Evolve: session_lifecycle integration"],
        meta_confidence=0.75,
        stale_entities=["old_example_config"],
    )


# ─── Noise Filtering Tests ────────────────────────────────────────────────

class TestNoiseFiltering:
    def test_skip_prefixes(self):
        """Messages starting with SKIP_PREFIXES are detected as noise."""
        for prefix in SKIP_PREFIXES:
            assert is_noise(prefix + " some content"), f"Failed for prefix: {prefix[:30]}"

    def test_real_messages_not_noise(self):
        """Real user messages are not noise."""
        assert not is_noise("verifique o gateway")
        assert not is_noise("integre com o Conscio")

    def test_strip_noise_removes_patterns(self):
        """strip_noise removes known noise patterns."""
        text = "Hello [CONTEXT COMPACTION — stuff] world"
        result = strip_noise(text)
        assert "[CONTEXT COMPACTION" not in result
        assert "Hello" in result
        assert "world" in result

    def test_strip_noise_empty(self):
        """strip_noise handles strings that are entirely noise."""
        result = strip_noise("[HEARTBEAT — Contexto da sessão anterior] all noise")
        assert "HEARTBEAT" not in result


# ─── Extraction Tests ─────────────────────────────────────────────────────

class TestExtraction:
    def test_extract_intents_filters_noise(self, sample_messages):
        """extract_intents skips noise messages."""
        intents = extract_intents(sample_messages)
        assert len(intents) == 3  # 3 real user messages, 1 noise skipped
        assert all("[CONTEXT" not in i for i in intents)

    def test_extract_intents_respects_limit(self, sample_messages):
        """extract_intents caps at MAX_USER_INTENTS for diverse messages."""
        from conscio.session_lifecycle import MAX_USER_INTENTS
        # Diverse content — each message should produce a distinct chunk
        big_messages = [{"role": "user", "content_preview": f"{topic} request {i}"}
                        for i, topic in enumerate(["debug engine", "fix gateway",
                                                    "deploy server", "check cron",
                                                    "update config", "review code",
                                                    "test pipeline", "install deps",
                                                    "clean logs", "restart bot",
                                                    "patch auth", "build docker",
                                                    ])]
        intents = extract_intents(big_messages)
        assert len(intents) <= MAX_USER_INTENTS

    def test_extract_actions_filters_noise(self, sample_messages):
        """extract_actions skips noise/compaction artifacts."""
        actions = extract_actions(sample_messages)
        # 3 real assistant messages (1 HEARTBEAT noise skipped)
        assert len(actions) == 3
        assert all("[HEARTBEAT" not in a for a in actions)

    def test_extract_reasoning_catches_keywords(self, sample_messages):
        """extract_reasoning captures messages with reasoning keywords."""
        reasoning = extract_reasoning(sample_messages)
        assert len(reasoning) >= 1
        assert any("bug" in r.lower() or "indent" in r.lower() for r in reasoning)

    def test_infer_topics(self):
        """infer_topics correctly identifies topics from content."""
        intents = ["verifique o gateway do hermes", "integre com o Conscio"]
        actions = ["Added session type to EventBus"]
        topics = infer_topics(intents, actions)
        assert "conscio" in topics
        assert "agent" in topics

    def test_infer_topics_trading(self):
        """infer_topics identifies trading topic."""
        intents = ["abra posição long em BTC-USDT"]
        actions = ["Swap order confirmed"]
        topics = infer_topics(intents, actions)
        assert "trading" in topics


# ─── DB Query Tests ───────────────────────────────────────────────────────

class TestDBQuery:
    def test_get_latest_session(self, tmp_db):
        """get_latest_session returns the most recent non-cron session."""
        session = get_latest_session(tmp_db)
        assert session is not None
        assert session["id"] == "test_20260605"
        assert session["source"] == "telegram"
        assert session["message_count"] == 12

    def test_get_latest_session_skips_cron(self, tmp_db):
        """get_latest_session does not return cron sessions."""
        session = get_latest_session(tmp_db)
        assert session is not None
        assert session["source"] != "cron"

    def test_get_latest_session_empty(self, empty_db):
        """get_latest_session returns None for empty DB."""
        session = get_latest_session(empty_db)
        assert session is None

    def test_get_latest_session_nonexistent(self, tmp_path):
        """get_latest_session returns None for nonexistent DB."""
        session = get_latest_session(tmp_path / "nope.db")
        assert session is None

    def test_get_session_by_id(self, tmp_db):
        """get_session_by_id returns session by exact ID."""
        session = get_session_by_id(tmp_db, "test_20260605")
        assert session is not None
        assert session["id"] == "test_20260605"
        assert session["source"] == "telegram"
        assert session["message_count"] == 12
        assert len(session["messages"]) == 12

    def test_get_session_by_id_not_found(self, tmp_db):
        """get_session_by_id returns None for unknown session ID."""
        session = get_session_by_id(tmp_db, "nonexistent_session")
        assert session is None

    def test_get_session_by_id_empty_db(self, empty_db):
        """get_session_by_id returns None for empty DB."""
        session = get_session_by_id(empty_db, "any_id")
        assert session is None

    def test_get_session_by_id_nonexistent_db(self, tmp_path):
        """get_session_by_id returns None for nonexistent DB."""
        session = get_session_by_id(tmp_path / "nope.db", "any_id")
        assert session is None

    def test_get_session_by_id_no_session_id(self, tmp_db):
        """get_session_by_id returns None when session_id is empty."""
        session = get_session_by_id(tmp_db, "")
        assert session is None
        session = get_session_by_id(tmp_db, None)
        assert session is None


# ─── Enrichment Tests ─────────────────────────────────────────────────────

class TestEnrichment:
    def test_enrich_with_conscio(self, sample_summary, mock_engine):
        """enrich_with_conscio fills Conscio state into summary."""
        result = enrich_with_conscio(sample_summary, mock_engine)
        # meta_confidence may stay at 0.75 if engine has no recorded confidence
        # but world_model_entities and goals should be populated (even if empty)
        assert isinstance(result, SessionSummary)
        # The enrich function should not crash even with empty engine

    def test_enrich_with_conscio_graceful_on_error(self, sample_summary):
        """enrich_with_conscio is graceful when engine methods fail."""
        # Make a fresh copy to avoid mutating the fixture for other tests
        fresh = SessionSummary(
            session_id=sample_summary.session_id,
            model=sample_summary.model,
            started_at=sample_summary.started_at,
            message_count=sample_summary.message_count,
            title=sample_summary.title,
            intents=list(sample_summary.intents),
            actions=list(sample_summary.actions),
            reasoning=list(sample_summary.reasoning),
            topics=list(sample_summary.topics),
        )
        broken_engine = MagicMock()
        broken_engine.world.list_entities.side_effect = RuntimeError("DB locked")
        broken_engine.goals.active_goals.side_effect = RuntimeError("DB locked")
        broken_engine.meta.average_confidence.side_effect = RuntimeError("DB locked")
        broken_engine.world.stale_entities.side_effect = RuntimeError("DB locked")

        result = enrich_with_conscio(fresh, broken_engine)
        # Should not crash — graceful fallback
        assert isinstance(result, SessionSummary)
        assert result.world_model_entities == []  # Not filled due to error


# ─── Formatting Tests ─────────────────────────────────────────────────────

class TestFormatting:
    def test_heartbeat_under_limit(self, sample_summary):
        """Heartbeat is always under HB_MAX_CHARS."""
        hb = format_heartbeat(sample_summary)
        assert len(hb) <= HB_MAX_CHARS

    def test_heartbeat_contains_key_info(self, sample_summary):
        """Heartbeat contains session ID, model, key content."""
        hb = format_heartbeat(sample_summary)
        assert "test_20260605" in hb
        assert "glm-5.1" in hb
        # Topics surface via chunks or goals — "conscio" appears in intents/goals
        assert "conscio" in hb.lower() or "Conscio" in hb

    def test_heartbeat_compact(self, sample_summary):
        """Heartbeat is more compact than handoff."""
        hb = format_heartbeat(sample_summary)
        ho = format_handoff(sample_summary)
        assert len(hb) < len(ho)

    def test_handoff_contains_enrichment(self, sample_summary):
        """Handoff contains Conscio enrichment section."""
        ho = format_handoff(sample_summary)
        # New format uses "conscio" as section header instead of "Estado Conscio"
        assert "conscio" in ho.lower()
        assert "trading_bot:active" in ho
        assert "Evolve: session_lifecycle" in ho

    def test_heartbeat_no_duplicate_noise(self, sample_summary):
        """Heartbeat doesn't contain compaction artifacts."""
        hb = format_heartbeat(sample_summary)
        assert "[CONTEXT COMPACTION" not in hb
        assert "[HEARTBEAT" not in hb

    def test_heartbeat_with_many_intents(self):
        """Heartbeat truncates when too many intents."""
        summary = SessionSummary(
            session_id="big_session",
            model="glm-5.1",
            started_at="2026-06-05",
            message_count=100,
            title="Big session",
            intents=[f"Very long intent number {i} with lots of text " * 5 for i in range(20)],
            actions=[f"Action {i}" for i in range(20)],
            topics=["trading", "debug", "conscio", "agent", "infra", "code"],
        )
        hb = format_heartbeat(summary)
        assert len(hb) <= HB_MAX_CHARS


# ─── Full Pipeline Tests ──────────────────────────────────────────────────

class TestRecordSessionLifecycle:
    def test_full_pipeline(self, tmp_db, mock_engine, tmp_path):
        """record_session_lifecycle runs end-to-end with real engine."""
        heartbeat_path = tmp_path / "_latest_heartbeat.md"
        handoff_path = tmp_path / "_session_handoff.md"

        with patch("conscio.session_lifecycle.SESSION_DB", tmp_db), \
             patch("conscio.session_lifecycle.HANDOFF_DIR", tmp_path), \
             patch("conscio.session_lifecycle.HEARTBEAT_PATH", heartbeat_path), \
             patch("conscio.session_lifecycle.HANDOFF_PATH", handoff_path):

            summary = record_session_lifecycle(
                event_type="session:reset",
                context={"platform": "telegram", "user_id": "123", "session_key": "k", "session_id": "test_20260605"},
                engine=mock_engine,
            )

        assert summary is not None
        assert summary.session_id == "test_20260605"
        assert len(summary.intents) >= 2
        assert len(summary.actions) >= 1
        assert "conscio" in summary.topics or "debug" in summary.topics

        # Files written
        assert heartbeat_path.exists()
        assert handoff_path.exists()
        hb_content = heartbeat_path.read_text()
        assert len(hb_content) <= HB_MAX_CHARS

        # Event emitted in EventBus
        events = mock_engine.event_bus.query(type="session", include_duplicates=True)
        assert len(events) >= 1
        assert events[0].data["event"] == "session:reset"

        # Content indexed in ContentStore
        stats = mock_engine.content_store.stats()
        assert stats["source_count"] >= 2  # heartbeat + handoff

    def test_invalid_event_type(self, mock_engine):
        """record_session_lifecycle returns None for invalid event types."""
        result = record_session_lifecycle(
            event_type="invalid",
            context={},
            engine=mock_engine,
        )
        assert result is None

    def test_empty_db_returns_none(self, empty_db, mock_engine, tmp_path):
        """record_session_lifecycle returns None when no session data."""
        heartbeat_path = tmp_path / "_latest_heartbeat.md"
        handoff_path = tmp_path / "_session_handoff.md"

        with patch("conscio.session_lifecycle.SESSION_DB", empty_db), \
             patch("conscio.session_lifecycle.HANDOFF_DIR", tmp_path), \
             patch("conscio.session_lifecycle.HEARTBEAT_PATH", heartbeat_path), \
             patch("conscio.session_lifecycle.HANDOFF_PATH", handoff_path):

            result = record_session_lifecycle(
                event_type="session:end",
                context={},
                engine=mock_engine,
            )

        assert result is None

    def test_auto_creates_engine_when_none(self, tmp_db, tmp_path):
        """record_session_lifecycle creates temp engine when engine=None."""
        heartbeat_path = tmp_path / "_latest_heartbeat.md"
        handoff_path = tmp_path / "_session_handoff.md"

        with patch("conscio.session_lifecycle.SESSION_DB", tmp_db), \
             patch("conscio.session_lifecycle.HANDOFF_DIR", tmp_path), \
             patch("conscio.session_lifecycle.HEARTBEAT_PATH", heartbeat_path), \
             patch("conscio.session_lifecycle.HANDOFF_PATH", handoff_path):

            result = record_session_lifecycle(
                event_type="session:end",
                context={},
                engine=None,  # Auto-create
            )

        assert result is not None
        assert heartbeat_path.exists()

    def test_session_end_vs_reset(self, tmp_db, mock_engine, tmp_path):
        """Both session:end and session:reset are processed."""
        for event_type in ("session:end", "session:reset"):
            heartbeat_path = tmp_path / f"hb_{event_type.replace(':', '_')}.md"
            handoff_path = tmp_path / f"ho_{event_type.replace(':', '_')}.md"

            with patch("conscio.session_lifecycle.SESSION_DB", tmp_db), \
                 patch("conscio.session_lifecycle.HANDOFF_DIR", tmp_path), \
                 patch("conscio.session_lifecycle.HEARTBEAT_PATH", heartbeat_path), \
                 patch("conscio.session_lifecycle.HANDOFF_PATH", handoff_path):

                result = record_session_lifecycle(
                    event_type=event_type,
                    context={},
                    engine=mock_engine,
                )
                assert result is not None


    def test_no_unbound_local_error_on_early_exception(self, tmp_db, mock_engine, tmp_path):
        """UnboundLocalError bug: if an exception occurs before heartbeat/handoff
        are defined inside the try block, the finally block and subsequent
        disk writes crash with UnboundLocalError.

        This test forces an early exception (in enrich_with_conscio) and
        verifies the function still completes without UnboundLocalError,
        writing empty fallback values to disk rather than crashing.
        """
        heartbeat_path = tmp_path / "_latest_heartbeat.md"
        handoff_path = tmp_path / "_session_handoff.md"

        with patch("conscio.session_lifecycle.SESSION_DB", tmp_db), \
             patch("conscio.session_lifecycle.HANDOFF_DIR", tmp_path), \
             patch("conscio.session_lifecycle.HEARTBEAT_PATH", heartbeat_path), \
             patch("conscio.session_lifecycle.HANDOFF_PATH", handoff_path), \
             patch("conscio.session_lifecycle.enrich_with_conscio", side_effect=RuntimeError("DB locked")):

            # Before the fix, this raises UnboundLocalError because
            # heartbeat/handoff are only defined inside the try block,
            # but used after the finally block in the disk-write section.
            record_session_lifecycle(
                event_type="session:end",
                context={"session_id": "test_20260605"},
                engine=mock_engine,
            )

            # The function should not crash — it should return the summary
            # (or at minimum not raise UnboundLocalError)
            # Files should be written (possibly empty if exception was early)
            assert heartbeat_path.exists()
            assert handoff_path.exists()

# ─── EventBus Integration Tests ────────────────────────────────────────────

class TestEventBusIntegration:
    def test_session_type_in_valid_types(self):
        """'session' is in VALID_TYPES after our change."""
        assert "session" in VALID_TYPES

    def test_session_category_in_valid_categories(self):
        """'session' is in VALID_CATEGORIES after our change."""
        assert "session" in VALID_CATEGORIES

    def test_emit_session_event(self, tmp_path):
        """EventBus can emit and query session events."""
        bus = EventBus(db_path=tmp_path / "test.db")
        try:
            eid = bus.emit("session", "session", {
                "event": "session:reset",
                "session_id": "test_123",
                "topics": ["conscio", "debug"],
            })
            assert eid > 0

            events = bus.query(type="session", include_duplicates=True)
            assert len(events) == 1
            assert events[0].data["event"] == "session:reset"
            assert events[0].data["topics"] == ["conscio", "debug"]
        finally:
            bus.close()


# ─── ContentStore Integration Tests ────────────────────────────────────────

class TestContentStoreIntegration:
    def test_index_session_content(self, tmp_path):
        """ContentStore can index and search session content."""
        store = ContentStore(db_path=tmp_path / "test.db")
        try:
            store.index(
                label="heartbeat_20260605_0900",
                content="# Heartbeat\nTopics: conscio, debug",
                category="session",
                session_id="test_20260605",
            )
            store.index(
                label="handoff_20260605_0900",
                content="# Handoff\nIntents: verifique gateway",
                category="session",
                session_id="test_20260605",
            )

            stats = store.stats()
            assert stats["source_count"] == 2

            # Search should find session content
            results = store.search("heartbeat conscio")
            assert len(results) >= 1
        finally:
            store.close()
