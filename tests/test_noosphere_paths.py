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
