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
