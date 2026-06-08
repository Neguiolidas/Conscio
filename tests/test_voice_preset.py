# tests/test_voice_preset.py
from conscio.voice_preset import resolve_voice_preset, available_presets


def test_resolve_known_preset():
    assert resolve_voice_preset("coherence-style") == "coherence-style"


def test_resolve_none_disables():
    assert resolve_voice_preset("none") == ""
    assert resolve_voice_preset("NONE") == ""


def test_resolve_empty_disables():
    assert resolve_voice_preset("") == ""


def test_resolve_missing_preset_disables():
    assert resolve_voice_preset("does-not-exist") == ""


def test_resolve_strips_whitespace():
    assert resolve_voice_preset("  coherence-style  ") == "coherence-style"


def test_available_includes_coherence_style():
    assert "coherence-style" in available_presets()


# --- v0.6 lifecycle surfacing -------------------------------------------------
from types import SimpleNamespace
from conscio.session_lifecycle import (
    SessionSummary, enrich_with_conscio, format_heartbeat,
)
from conscio.coherence import CoherenceReport, Dissonance


def _fake_engine_with_coherence():
    rep = CoherenceReport(
        score=0.41,
        dimensions={"epistemic": 0.2, "reality": 1.0, "ontological": 1.0, "temporal": 1.0},
        dissonances=[Dissonance("epistemic", 0.2, 0.8, "x")],
        dominant=Dissonance("epistemic", 0.2, 0.8, "x"),
    )
    return SimpleNamespace(
        last_coherence=rep,
        voice_preset="coherence-style",
        world=SimpleNamespace(list_entities=lambda limit=5: [], stale_entities=lambda: []),
        goals=SimpleNamespace(active_goals=lambda: []),
        meta=SimpleNamespace(average_confidence=lambda: 0.5),
    )


def test_enrich_sets_coherence_and_voice():
    summary = SessionSummary(session_id="s", model="glm")
    enrich_with_conscio(summary, _fake_engine_with_coherence())
    assert summary.coherence == 0.41
    assert summary.coherence_note == "epistemic"
    assert summary.voice == "coherence-style"


def test_heartbeat_renders_coherence_marker():
    summary = SessionSummary(
        session_id="s", model="glm", coherence=0.41, coherence_note="epistemic")
    assert "▷ coherence: 0.41 dominant: epistemic" in format_heartbeat(summary)


def test_heartbeat_renders_voice_marker():
    summary = SessionSummary(session_id="s", model="glm", voice="coherence-style")
    assert "⊙ voice: coherence-style" in format_heartbeat(summary)


def test_heartbeat_omits_coherence_when_none():
    summary = SessionSummary(session_id="s", model="glm")
    assert "coherence:" not in format_heartbeat(summary)
