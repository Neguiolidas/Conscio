# tests/test_noosphere_paths.py
from pathlib import Path

from conscio.noosphere import paths


def test_hermes_home_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/tmp/hh")
    assert paths.hermes_home() == Path("/tmp/hh")
    assert paths.default_storage() == Path("/tmp/hh/consciousness")
    assert paths.default_noosphere_db() == Path("/tmp/hh/noosphere.db")


def test_resolvers_prefer_explicit():
    assert paths.resolve_storage("/x") == Path("/x")
    assert paths.resolve_noosphere("/y/n.db") == Path("/y/n.db")


def test_per_instance_paths():
    s = Path("/s")
    assert paths.instance_path(s) == Path("/s/instance.json")
    assert paths.conscio_db_path(s) == Path("/s/conscio.db")
    assert paths.quarantine_db_path(s) == Path("/s/noosphere_quarantine.db")


# ── v4.5.3: neutral home + legacy preservation ─────────────────────────

def test_conscio_home_env_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("CONSCIO_HOME", "/tmp/conscio")
    monkeypatch.setenv("HERMES_HOME", "/tmp/legacy")
    assert paths.conscio_home() == Path("/tmp/conscio")


def test_hermes_home_is_legacy_override(monkeypatch, tmp_path):
    # no CONSCIO_HOME, but HERMES_HOME set -> legacy override still works
    monkeypatch.delenv("CONSCIO_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/tmp/legacy")
    assert paths.conscio_home() == Path("/tmp/legacy")


def test_hermes_home_alias_tracks_conscio_home(monkeypatch):
    monkeypatch.setenv("CONSCIO_HOME", "/tmp/conscio")
    assert paths.hermes_home() == paths.conscio_home() == Path("/tmp/conscio")


def test_neutral_default_when_nothing_set(monkeypatch, tmp_path):
    # both unset + no ~/.hermes detected in HOME -> neutral ~/.conscio
    monkeypatch.delenv("CONSCIO_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no .hermes here
    expected = tmp_path / ".conscio"
    assert paths.conscio_home() == expected
