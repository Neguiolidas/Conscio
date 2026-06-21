# tests/test_hub_providers.py
import pytest

from conscio.hub import providers


# ---------------------------------------------------------------------------
# Task 6: BUILTIN, KNOWN_MODELS, plugins(), catalog()
# ---------------------------------------------------------------------------

def test_builtin_has_six_types():
    assert set(providers.BUILTIN) == {
        "lmstudio", "ollama", "openai", "anthropic", "gemini", "openai-compat"}


def test_catalog_shape_and_redaction():
    cat = providers.catalog(
        {"adapter": {"type": "openai"},
         "providers": {"logfare": {"type": "openai-compat",
                                   "base_url": "https://x/v1",
                                   "api_key": "leak"}}})
    assert cat["builtin"] and isinstance(cat["plugins"], list)
    assert "api_key" not in cat["custom"]["logfare"]      # redacted


def test_plugins_is_list(monkeypatch):
    monkeypatch.setattr("conscio.plugins.discover_adapters", lambda: {"my": object})
    assert providers.plugins() == ["my"]


# ---------------------------------------------------------------------------
# Task 7: resolve_provider()
# ---------------------------------------------------------------------------

def test_resolve_custom_name():
    cfg = {"providers": {"logfare": {"type": "openai-compat",
                                     "base_url": "https://x/v1"}}}
    assert providers.resolve_provider(cfg, "logfare")["base_url"] == "https://x/v1"


def test_resolve_builtin_type_uses_default_base_url():
    pc = providers.resolve_provider({}, "ollama")
    assert pc == {"type": "ollama", "base_url": "http://localhost:11434"}


def test_resolve_unknown_raises():
    with pytest.raises(KeyError):
        providers.resolve_provider({}, "nope")


# ---------------------------------------------------------------------------
# Task 8: _get_json() + probe_models() per-type dispatch + fallback
# ---------------------------------------------------------------------------

def test_probe_openai_compat(monkeypatch):
    monkeypatch.setattr(providers, "_get_json",
                        lambda url, **k: {"data": [{"id": "glm-5.1"}, {"id": "x"}]})
    out = providers.probe_models({"type": "openai-compat", "base_url": "https://h/v1"},
                                 refresh=True)
    assert out["models"] == ["glm-5.1", "x"] and out["source"] == "api"


def test_probe_ollama_tags(monkeypatch):
    monkeypatch.setattr(providers, "_get_json",
                        lambda url, **k: {"models": [{"name": "llama3.1"}]})
    out = providers.probe_models({"type": "ollama", "base_url": "http://h:11434"},
                                 refresh=True)
    assert out["models"] == ["llama3.1"]


def test_probe_gemini_strips_prefix(monkeypatch):
    monkeypatch.setattr(providers, "_get_json",
                        lambda url, **k: {"models": [{"name": "models/gemini-2.5-pro"}]})
    out = providers.probe_models({"type": "gemini", "base_url": "https://g"},
                                 refresh=True)
    assert out["models"] == ["gemini-2.5-pro"]


def test_probe_anthropic_is_fallback():
    out = providers.probe_models({"type": "anthropic"}, refresh=True)
    assert out["source"] == "fallback" and "claude-opus-4-8" in out["models"]


def test_probe_network_error_falls_back(monkeypatch):
    def boom(url, **k):
        raise OSError("down")
    monkeypatch.setattr(providers, "_get_json", boom)
    out = providers.probe_models({"type": "openai", "base_url": "https://h/v1"},
                                 refresh=True)
    assert out["source"] == "fallback" and out["probed"] is False
