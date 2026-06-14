"""Tests for voice preset rendering in heartbeat/handoff."""
from conscio.session_lifecycle import SessionSummary, format_heartbeat


def test_heartbeat_renders_coherence_marker():
    summary = SessionSummary(
        session_id="s", model="glm", coherence=0.41, coherence_note="epistemic")
    hb = format_heartbeat(summary)
    assert "0.41" in hb
    assert "epistemic" in hb


def test_heartbeat_renders_voice_marker():
    summary = SessionSummary(session_id="s", model="glm", voice="coherence-style")
    hb = format_heartbeat(summary)
    assert "coherence-style" in hb


def test_heartbeat_omits_coherence_when_none():
    summary = SessionSummary(session_id="s", model="glm")
    assert "coherence:" not in format_heartbeat(summary)
