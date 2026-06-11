"""Verify record_session_lifecycle triggers a dream (Mitosis → Dream)."""
from unittest.mock import MagicMock

import conscio.session_lifecycle as sl
from conscio.session_lifecycle import record_session_lifecycle


def test_dream_called_after_handoff(monkeypatch, tmp_path):
    # Stub session extraction so no real state.db is needed.
    fake_session = {
        "id": "sess-123", "model": "glm-5.1", "started_at": "2026-06-05",
        "message_count": 3, "title": "T",
        "messages": [
            {"role": "user", "content": "hello there friend"},
            {"role": "assistant", "content": "responding to the user now"},
        ],
    }
    monkeypatch.setattr(sl, "get_latest_session", lambda db: fake_session)

    engine = MagicMock()
    engine.world.stale_entities.return_value = []
    engine.world.list_entities.return_value = []
    engine.goals.active_goals.return_value = []
    engine.meta.average_confidence.return_value = 0.5
    engine.output_filter = MagicMock()
    engine.output_filter.apply = lambda x: x

    # Pass handoff_dir explicitly (new API) — no monkeypatch on module constants needed
    summary = record_session_lifecycle(
        "session:reset", {}, engine=engine, handoff_dir=tmp_path
    )

    assert summary is not None
    engine.dream.assert_called_once()
    # Handoff still written to the provided directory
    assert (tmp_path / "_latest_heartbeat.md").exists()


def test_handoff_disabled(monkeypatch, tmp_path):
    """When handoff_dir=None, no files are written but pipeline still runs."""
    fake_session = {
        "id": "sess-456", "model": "glm-5.1", "started_at": "2026-06-05",
        "message_count": 3, "title": "T",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    }
    monkeypatch.setattr(sl, "get_latest_session", lambda db: fake_session)

    engine = MagicMock()
    engine.world.stale_entities.return_value = []
    engine.world.list_entities.return_value = []
    engine.goals.active_goals.return_value = []
    engine.meta.average_confidence.return_value = 0.5
    engine.output_filter = MagicMock()
    engine.output_filter.apply = lambda x: x

    # handoff_dir=None disables file writes
    summary = record_session_lifecycle(
        "session:end", {}, engine=engine, handoff_dir=None
    )

    assert summary is not None
    # No files written
    assert not (tmp_path / "_session_handoff.md").exists()
    assert not (tmp_path / "_latest_heartbeat.md").exists()
