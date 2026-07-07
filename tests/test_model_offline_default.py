"""v1.4 'Attune' — model-context detection must be offline & deterministic by default.

These tests pin the invariant the v1.3-era code held and the auto-detect commits
broke: building model info for a *known* model with no explicit override performs
ZERO filesystem/network I/O and returns the curated registry value on every
machine. All host-state reads (config file, LM Studio state, GGUF scan) are
reachable only behind an explicit opt-in (`autodetect=True` / CONSCIO_AUTODETECT).
"""

import pytest

import conscio.models as m


def _tripwire(*_a, **_k):
    raise AssertionError("detect() touched host state on the default (offline) path")


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    # Isolate from the developer's environment so the tests are deterministic.
    monkeypatch.delenv("CONSCIO_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("CONSCIO_AUTODETECT", raising=False)


def _arm_tripwires(monkeypatch):
    monkeypatch.setattr(m.ModelRegistry, "_read_config_context",
                        classmethod(lambda cls, n: _tripwire()))
    monkeypatch.setattr(m.ModelRegistry, "query_context_from_lmstudio",
                        classmethod(lambda cls, n: _tripwire()))
    monkeypatch.setattr(m.ModelRegistry, "query_context_from_gguf",
                        classmethod(lambda cls, n, search_dirs=None: _tripwire()))


def test_known_model_default_is_offline_and_registry_truth(monkeypatch):
    """A known model, no override → registry value, no host-state probe called."""
    _arm_tripwires(monkeypatch)
    info = m.ModelRegistry.detect("glm-5.1")
    assert info.context_window == 200_000          # curated registry, deterministic


def test_unknown_model_default_is_offline_heuristic(monkeypatch):
    """An unknown model, no override → name heuristic, no GGUF/host scan."""
    _arm_tripwires(monkeypatch)
    info = m.ModelRegistry.detect("mystery-256k")
    assert info.context_window == 256_000          # parsed from the name, no I/O


def test_env_context_window_stays_default_on(monkeypatch):
    """CONSCIO_CONTEXT_WINDOW is explicit process config (no I/O) → still honored."""
    _arm_tripwires(monkeypatch)
    monkeypatch.setenv("CONSCIO_CONTEXT_WINDOW", "42000")
    info = m.ModelRegistry.detect("glm-5.1")
    assert info.context_window == 42_000


def test_autodetect_opt_in_consults_host_state(monkeypatch):
    """With autodetect=True the host-state path IS consulted (config wins here)."""
    monkeypatch.setattr(m.ModelRegistry, "_read_config_context",
                        classmethod(lambda cls, n: 777_000))
    info = m.ModelRegistry.detect("glm-5.1", autodetect=True)
    assert info.context_window == 777_000
