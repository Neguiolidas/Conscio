# tests/test_session_lifecycle_battery.py
"""v1.9 deep battery — session_lifecycle adversarial edge cases.

B-010: a session with a NULL `title` column makes format_handoff evaluate
       `summary.title[:50]` = `None[:50]` → TypeError. record_session_lifecycle
       catches it (L839), so the handoff silently becomes "" and is persisted —
       clobbering the prior handoff AND skipping the handoff-index + reflection.
       format_heartbeat already guards title (`... if summary.title else ""`);
       format_handoff did not. NULL titles are realistic (cron/fresh sessions:
       `session.get("title", "N/A")` returns None when the column is NULL).
"""
import sqlite3

from conscio.session_lifecycle import (
    SessionSummary, format_handoff, format_heartbeat, record_session_lifecycle,
)


def test_try_break_none_title_handoff_no_crash():
    s = SessionSummary(session_id="sid1234567890", model="glm-5.1",
                       message_count=4, title=None)
    out = format_handoff(s)                 # was TypeError: None[:50]
    assert "sid1234567890"[:16] in out


def test_try_break_none_title_heartbeat_no_crash():
    # heartbeat already guarded — characterization lock (must stay safe)
    s = SessionSummary(session_id="sid1234567890", model="glm-5.1",
                       message_count=4, title=None)
    assert format_heartbeat(s)


def _state_db_with_null_title(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, "
                 "model TEXT, started_at TEXT, message_count INTEGER, title TEXT)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "session_id TEXT, role TEXT, content TEXT)")
    conn.execute("INSERT INTO sessions (id, source, model, started_at, "
                 "message_count, title) VALUES (?,?,?,?,?,NULL)",
                 ("s_null", "telegram", "glm-5.1", "2026-06-18T08:00:00", 4))
    for role, content in [
        ("user", "preciso corrigir o bug do gateway agora"),
        ("assistant", "Corrigi o gateway. O timeout estava errado no engine."),
        ("user", "rode os testes pra confirmar"),
        ("assistant", "Testes passaram. Engine operacional."),
    ]:
        conn.execute("INSERT INTO messages (session_id, role, content) "
                     "VALUES (?,?,?)", ("s_null", role, content))
    conn.commit()
    conn.close()
    return db


def test_try_break_null_title_persists_real_handoff(tmp_path):
    """Integration: a NULL-title session must still produce a NON-empty handoff
    (B-010 made it silently empty) and index BOTH heartbeat + handoff (the
    handoff index is skipped when format_handoff crashes)."""
    from conscio.engine import ConsciousnessEngine
    db = _state_db_with_null_title(tmp_path)
    handoff_dir = tmp_path / "handoff"
    engine = ConsciousnessEngine(model_name="glm-5.1",
                                 storage_path=tmp_path / "cstore")
    try:
        summary = record_session_lifecycle(
            "session:end", {"session_id": "s_null"},
            engine=engine, session_db=db, handoff_dir=handoff_dir,
        )
        assert summary is not None
        handoff_text = (handoff_dir / "_session_handoff.md").read_text()
        assert handoff_text.strip(), "NULL title clobbered handoff to empty (B-010)"
        assert engine.content_store.stats()["source_count"] >= 2
    finally:
        engine.close()
