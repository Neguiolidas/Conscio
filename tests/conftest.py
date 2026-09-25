"""Shared test isolation.

Model/context resolution reads ~/.config/conscio/config.json. On a developer
machine that file exists (and may pin windows, e.g. glm-5.2 -> 1048576), so a
test that detects a model would pass on CI (no file) but flake locally. Isolate
every test from that real file by default; tests that specifically exercise
config reading monkeypatch these paths again inside the test (which wins).
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_conscio_config(monkeypatch, tmp_path_factory):
    nowhere = tmp_path_factory.mktemp("no_conscio_cfg") / "config.json"
    import conscio.adapter_config as _ac
    import conscio.models as _m
    monkeypatch.setattr(_m.ModelRegistry, "_CONFIG_PATHS", [nowhere], raising=False)
    monkeypatch.setattr(_ac, "_CONFIG_PATHS", [nowhere], raising=False)


@pytest.fixture(autouse=True)
def _isolate_relay_root(monkeypatch, tmp_path_factory):
    """Same doctrine for the relay directory, which is a *write*.

    The directory root defaults to ~/.conscio/relay, so a test that builds
    relay-enabled bindings published cards named `A`/`X` into the developer's
    live directory — where a real agent would then see them as peers. A test
    must never be able to hand production a ghost peer.
    """
    from conscio.liaison import directory
    monkeypatch.setenv(directory.RELAY_ROOT_ENV,
                       str(tmp_path_factory.mktemp("relay_root")))


@pytest.fixture(autouse=True)
def _isolate_host_identity_env(monkeypatch):
    """Isolate tests from ambient host-identity environment variables.

    When running inside host runtimes (ZCode, Antigravity, Claude Code,
    Hermes, OpenCode), ambient process environment signals can leak into
    tests that derive host identity or durable space paths. We clean all
    host signals before each test; tests requiring specific signals set
    them explicitly via monkeypatch.setenv.
    """
    from conscio.mcp import host_identity as _hi

    host_keys = (
        _hi._ZCODE_PRIMARY_KEYS
        + _hi._ZCODE_FALLBACK_KEYS
        + _hi._ANTIGRAVITY_PRIMARY_KEYS
        + _hi._ANTIGRAVITY_FALLBACK_KEYS
        + _hi._CLAUDE_NATIVE_KEYS
        + _hi._CLAUDE_PLUGIN_KEYS
        + _hi._HERMES_KEYS
        + _hi._OPENCODE_KEYS
    )
    for k in host_keys:
        monkeypatch.delenv(k, raising=False)

    for k in list(os.environ.keys()):
        if k.startswith("CONSCIO_IDENTITY_"):
            monkeypatch.delenv(k, raising=False)
