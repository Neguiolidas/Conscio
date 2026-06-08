"""Verify record_session_lifecycle triggers a dream (Mitosis → Dream)."""
from unittest.mock import MagicMock

import conscio.session_lifecycle as sl
from conscio.session_lifecycle import record_session_lifecycle, SessionSummary


def test_dream_called_after_handoff(monkeypatch, tmp_path):
    # Stub Hermes session extraction so no real state.db is needed.
    fake_session = {
        "id": "sess-123", "model": "glm-5.1", "started_at": "2026-06-05",
        "message_count": 3, "title": "T",
        "messages": [
            {"role": "user", "content": "hello there friend"},
            {"role": "assistant", "content": "responding to the user now"},
        ],
    }
    monkeypatch.setattr(sl, "get_latest_session", lambda db: fake_session)
    # Redirect disk writes into tmp so the test never touches ~/mempalace.
    monkeypatch.setattr(sl, "MEMPALACE_DIR", tmp_path)
    monkeypatch.setattr(sl, "HANDOFF_PATH", tmp_path / "handoff.md")
    monkeypatch.setattr(sl, "HEARTBEAT_PATH", tmp_path / "heartbeat.md")

    engine = MagicMock()
    engine.world.stale_entities.return_value = []
    engine.world.list_entities.return_value = []
    engine.goals.active_goals.return_value = []
    engine.meta.average_confidence.return_value = 0.5
    # Mock output_filter to pass through strings unchanged
    engine.output_filter = MagicMock()
    engine.output_filter.apply = lambda x: x

    summary = record_session_lifecycle("session:reset", {}, engine=engine)

    assert summary is not None
    engine.dream.assert_called_once()
    # Handoff still written
    assert (tmp_path / "heartbeat.md").exists()
