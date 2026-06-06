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


def test_available_includes_coherence_style():
    assert "coherence-style" in available_presets()
