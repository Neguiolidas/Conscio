"""v2.14 model-context detection — auto-detect ON by default.

Contract (Option A — context-first):

  * A known model, with no override/pin, resolves from the registry and does
    NOT probe ambient host state (LM Studio / GGUF scan).
  * An explicit config-file pin (``models``/``context_window``) is treated as
    user configuration, same tier as ``CONSCIO_CONTEXT_WINDOW`` — it wins even
    for a known model. It is NOT ambient host probing.
  * Ambient host probing (LM Studio state, GGUF $HOME scan) is reached only for
    UNKNOWN models, and only with autodetect on (default). Disable with
    autodetect=False or CONSCIO_AUTODETECT=0.
"""

import pytest

import conscio.models as m


def _tripwire(*_a, **_k):
    raise AssertionError("detect() probed ambient host state on an offline path")


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch, tmp_path):
    # Isolate from the developer's environment so the tests are deterministic:
    # no ambient env vars and no real ~/.config/conscio/config.json leaking in.
    monkeypatch.delenv("CONSCIO_CONTEXT_WINDOW", raising=False)
    monkeypatch.delenv("CONSCIO_AUTODETECT", raising=False)
    monkeypatch.setattr(m.ModelRegistry, "_CONFIG_PATHS", [tmp_path / "nope.json"])


def _arm_ambient_tripwires(monkeypatch):
    """Arm only the TRUE host-state probes (LM Studio + GGUF). A config-file
    pin is explicit user config, not ambient probing, so it is NOT armed."""
    monkeypatch.setattr(m.ModelRegistry, "query_context_from_lmstudio",
                        classmethod(lambda cls, n: _tripwire()))
    monkeypatch.setattr(m.ModelRegistry, "query_context_from_gguf",
                        classmethod(lambda cls, n, search_dirs=None: _tripwire()))


def test_known_model_default_is_registry_truth_no_ambient_probe(monkeypatch):
    """A known model, no pin → registry value, no LM Studio / GGUF probe."""
    _arm_ambient_tripwires(monkeypatch)
    info = m.ModelRegistry.detect("glm-5.1")
    assert info.context_window == 200_000          # curated registry, deterministic


def test_config_pin_overrides_known_model(monkeypatch, tmp_path):
    """An explicit config pin wins over the registry even for a known model."""
    _arm_ambient_tripwires(monkeypatch)
    cfg = tmp_path / "config.json"
    cfg.write_text('{"models": {"glm-5.1": {"context_window": 50000}}}')
    monkeypatch.setattr(m.ModelRegistry, "_CONFIG_PATHS", [cfg])
    info = m.ModelRegistry.detect("glm-5.1")
    assert info.context_window == 50_000           # pin beats the 200k registry


def test_unknown_model_offline_with_autodetect_false(monkeypatch):
    """An unknown model, autodetect=False → name heuristic, no GGUF/host scan."""
    _arm_ambient_tripwires(monkeypatch)
    info = m.ModelRegistry.detect("mystery-256k", autodetect=False)
    assert info.context_window == 256_000          # parsed from the name, no I/O


def test_env_context_window_stays_default_on(monkeypatch):
    """CONSCIO_CONTEXT_WINDOW is explicit process config (no I/O) → still honored."""
    _arm_ambient_tripwires(monkeypatch)
    monkeypatch.setenv("CONSCIO_CONTEXT_WINDOW", "42000")
    info = m.ModelRegistry.detect("glm-5.1")
    assert info.context_window == 42_000


def test_autodetect_opt_in_consults_host_state(monkeypatch):
    """With autodetect=True the host-state path IS consulted (config wins here)."""
    monkeypatch.setattr(m.ModelRegistry, "_read_config_context",
                        classmethod(lambda cls, n: 777_000))
    # Use an UNKNOWN model so the full autodetect path is reached.
    info = m.ModelRegistry.detect("mystery-unknown", autodetect=True)
    assert info.context_window == 777_000
