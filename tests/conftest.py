"""Shared test isolation.

Model/context resolution reads ~/.config/conscio/config.json. On a developer
machine that file exists (and may pin windows, e.g. glm-5.2 -> 1048576), so a
test that detects a model would pass on CI (no file) but flake locally. Isolate
every test from that real file by default; tests that specifically exercise
config reading monkeypatch these paths again inside the test (which wins).
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_conscio_config(monkeypatch, tmp_path_factory):
    nowhere = tmp_path_factory.mktemp("no_conscio_cfg") / "config.json"
    import conscio.models as _m
    import conscio.adapter_config as _ac
    monkeypatch.setattr(_m.ModelRegistry, "_CONFIG_PATHS", [nowhere], raising=False)
    monkeypatch.setattr(_ac, "_CONFIG_PATHS", [nowhere], raising=False)
