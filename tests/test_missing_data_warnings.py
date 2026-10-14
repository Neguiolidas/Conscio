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
    # Which kind: a file that is NOT THERE is the 4.9.2 packaging bug, and the
    # message must not be confusable with the corrupt-file case.
    assert "not found" in warned[0].getMessage()
    assert "unreadable" not in warned[0].getMessage()


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
    assert "not found" in warned[0].getMessage()
    assert "unreadable" not in warned[0].getMessage()


def test_corrupt_axis_pack_warns_unreadable_not_missing(
        tmp_path, monkeypatch, caplog) -> None:
    """A pack that IS there but will not parse is a different failure.

    Advisory loading means the process keeps running degraded — but silently
    degrading was the very bug this release fixes. The warning names the path
    and the error, and it says "unreadable", never "not found".
    """
    monkeypatch.setattr(axis_pack, "AXES_DIR", tmp_path)
    broken = tmp_path / "core.json"
    broken.write_text("{ not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=AXES):
        assert axis_pack.load_axes(["core"]) == []

    warned = _warnings(caplog, AXES)
    assert len(warned) == 1, f"one warning per process, got {len(warned)}"
    assert "unreadable" in warned[0].getMessage()
    assert "not found" not in warned[0].getMessage()
    assert str(broken) in caplog.text, caplog.text


def test_binary_axis_pack_warns_unreadable_with_the_decode_error(
        tmp_path, monkeypatch, caplog) -> None:
    """B-013 survived as a crash fix; the silence never got fixed. It does now."""
    monkeypatch.setattr(axis_pack, "AXES_DIR", tmp_path)
    (tmp_path / "core.json").write_bytes(b"\xff\xfe\x00 not utf8 json \xff")

    with caplog.at_level(logging.WARNING, logger=AXES):
        assert axis_pack.load_axes(["core"]) == []

    assert "unreadable" in caplog.text, caplog.text
    assert "decode" in caplog.text.lower(), (
        f"the error itself must be named, got: {caplog.text}")


def test_voice_preset_that_is_not_a_file_warns_unreadable(
        tmp_path, monkeypatch, caplog) -> None:
    """The install shipped a PATH that is not a usable file — not a missing one.

    voice_preset never reads the preset's CONTENT (the framework only surfaces
    its name), so this branch is the whole of "corrupt" for a voice preset.
    """
    monkeypatch.setattr(voice_preset, "PRESET_DIR", tmp_path)
    (tmp_path / "coherence-style.md").mkdir()      # exists, is a directory

    with caplog.at_level(logging.WARNING, logger=VOICE):
        assert voice_preset.resolve_voice_preset("coherence-style") == ""

    warned = _warnings(caplog, VOICE)
    assert len(warned) == 1, f"one warning per process, got {len(warned)}"
    assert "unreadable" in warned[0].getMessage()
    assert "not found" not in warned[0].getMessage()
    assert str(tmp_path / "coherence-style.md") in caplog.text


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
