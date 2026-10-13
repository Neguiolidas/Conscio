"""Silent degradation must be visible: a missing data file warns, an off switch does not.

Three loaders are advisory by design, so an install that lost its data used to
get '' and [] with nobody saying a word — indistinguishable from an operator
who asked for no voice and no axes. Both cases are pinned here:

- a file the install should have shipped: one warning per process, naming the
  exact path that is missing;
- the deliberate off switch: silence, because nothing is wrong.
"""
from __future__ import annotations

import importlib
import logging

import pytest

from conscio import axis_pack, voice_preset
from conscio.engine import ConsciousnessEngine

VOICE = "conscio.voice_preset"
AXES = "conscio.axis_pack"
HUB = "conscio.hub.providers"


@pytest.fixture(autouse=True)
def _open_warn_once_latch():
    """The once-per-process latch is module-global; open it for every test.

    Without this, a test asserting silence can pass merely because an earlier
    test already spent the warning — a tooth that only works in some orders is
    not a tooth.
    """
    voice_preset._warned.clear()
    axis_pack._warned.clear()
    yield
    voice_preset._warned.clear()
    axis_pack._warned.clear()


def _warnings(caplog, logger: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == logger and r.levelno >= logging.WARNING]


def test_missing_voice_preset_warns_once_naming_the_path(
        tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(voice_preset, "PRESET_DIR", tmp_path)
    missing = tmp_path / "coherence-style.md"

    with caplog.at_level(logging.WARNING, logger=VOICE):
        assert voice_preset.resolve_voice_preset("coherence-style") == ""
        assert voice_preset.resolve_voice_preset("coherence-style") == ""

    warned = _warnings(caplog, VOICE)
    assert len(warned) == 1, f"one warning per process, got {len(warned)}"
    assert str(missing) in caplog.text, caplog.text


def test_missing_axis_pack_warns_once_naming_the_path(
        tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(axis_pack, "AXES_DIR", tmp_path)
    missing = tmp_path / "core.json"

    with caplog.at_level(logging.WARNING, logger=AXES):
        assert axis_pack.load_axes(["core"]) == []
        assert axis_pack.load_axes(["core"]) == []

    warned = _warnings(caplog, AXES)
    assert len(warned) == 1, f"one warning per process, got {len(warned)}"
    assert str(missing) in caplog.text, caplog.text


def test_voice_off_switch_via_engine_stays_silent(tmp_path, caplog) -> None:
    """`voice_preset="none"` is the operator saying no — no warning is due."""
    with caplog.at_level(logging.WARNING, logger=VOICE):
        eng = ConsciousnessEngine(
            model_name="glm-5.1", storage_path=str(tmp_path), voice_preset="none")
    assert eng.voice_preset == ""
    assert _warnings(caplog, VOICE) == [], caplog.text


def test_voice_off_switch_via_env_stays_silent(tmp_path, monkeypatch, caplog) -> None:
    """CONSCIO_VOICE_PRESET set-but-empty reaches the same branch (engine.py)."""
    monkeypatch.setenv("CONSCIO_VOICE_PRESET", "")
    with caplog.at_level(logging.WARNING, logger=VOICE):
        eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=str(tmp_path))
    assert eng.voice_preset == ""
    assert _warnings(caplog, VOICE) == [], caplog.text


def test_empty_axis_pack_list_is_the_off_switch_and_stays_silent(
        tmp_path, monkeypatch, caplog) -> None:
    """`axis_packs=[]` (semantic.SemanticEngine) asks for no axes at all."""
    monkeypatch.setattr(axis_pack, "AXES_DIR", tmp_path)   # an empty directory
    with caplog.at_level(logging.WARNING, logger=AXES):
        assert axis_pack.load_axes([]) == []
    assert _warnings(caplog, AXES) == [], caplog.text


def test_hub_mirror_fallback_warns_instead_of_staying_quiet(
        monkeypatch, caplog, tmp_path) -> None:
    """Import-time fallback: the mirror keeps probes alive, the warning says why."""
    from conscio import provider_specs
    from conscio.hub import providers as hub_providers

    monkeypatch.setattr(provider_specs, "_JSON_PATH", tmp_path / "absent.json")
    provider_specs.load_provider_specs.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger=HUB):
            importlib.reload(hub_providers)
        warned = _warnings(caplog, HUB)
        assert len(warned) == 1, f"expected one fallback warning, got {len(warned)}"
        assert "absent.json" in caplog.text, caplog.text
        assert hub_providers._DEFAULT_BASE_URL.get("openai") == "https://api.openai.com/v1"
    finally:
        provider_specs.load_provider_specs.cache_clear()
        importlib.reload(hub_providers)
