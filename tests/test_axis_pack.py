# tests/test_axis_pack.py
from conscio.axis_pack import (
    load_axes, resolve_axis_packs, available_axis_packs, DEFAULT_PACKS,
)


def test_load_default_core_has_availability():
    axes = load_axes()  # default ['core']
    names = {a["axis"] for a in axes}
    assert "availability" in names and "ownership" in names


def test_load_returns_axis_dicts_with_poles():
    ax = next(a for a in load_axes() if a["axis"] == "availability")
    assert "operational" in ax["positive"]
    assert "offline" in ax["negative"]


def test_merge_multiple_packs_is_additive():
    once = load_axes(["core"])
    twice = load_axes(["core", "core"])
    assert len(twice) == 2 * len(once)


def test_missing_pack_is_skipped_not_fatal():
    assert load_axes(["core", "does-not-exist"]) == load_axes(["core"])


def test_resolve_param_beats_env(monkeypatch):
    monkeypatch.setenv("CONSCIO_AXIS_PACKS", "legal,video")
    assert resolve_axis_packs(["core"]) == ["core"]


def test_resolve_env_beats_default(monkeypatch):
    monkeypatch.setenv("CONSCIO_AXIS_PACKS", "core, legal")
    assert resolve_axis_packs(None) == ["core", "legal"]


def test_resolve_default_when_unset(monkeypatch):
    monkeypatch.delenv("CONSCIO_AXIS_PACKS", raising=False)
    assert resolve_axis_packs(None) == DEFAULT_PACKS


def test_available_axis_packs_lists_core():
    assert "core" in available_axis_packs()
