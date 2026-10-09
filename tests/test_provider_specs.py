"""Tests for the provider spec single source of truth (v4.9, item 7)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conscio import provider_specs
from conscio.hub.config import known_types
from conscio.model_providers_json_check import JSON_PATH


def test_json_file_exists_and_is_object() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and len(data) >= 6


def test_spec_table_loads_and_validates() -> None:
    specs = provider_specs.load_provider_specs()
    assert set(specs) >= {"lmstudio", "ollama", "openai", "anthropic",
                          "gemini", "openai-compat"}
    for name, spec in specs.items():
        assert spec["base_url"], f"{name} base_url must be non-empty"
        assert "probe" in spec and "models_path" in spec


def test_hub_known_types_reads_the_json() -> None:
    """The hub's known_types() derives from the same file (one truth)."""
    types = known_types()
    assert "multi-fallback" in types
    for name in provider_specs.provider_types():
        assert name in types, f"JSON provider {name} missing from known_types()"


def test_default_base_url_lookup() -> None:
    assert provider_specs.default_base_url("openai") \
        == "https://api.openai.com/v1"
    with pytest.raises(KeyError):
        provider_specs.default_base_url("nonexistent")


def test_load_is_cached_single_process() -> None:
    a = provider_specs.load_provider_specs()
    b = provider_specs.load_provider_specs()
    assert a is b  # lru_cache: one read per process


def test_fail_fast_on_malformed(monkeypatch: pytest.MonkeyPatch,
                                 tmp_path: Path) -> None:
    """A malformed file raises — never silently falls back."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(provider_specs, "_JSON_PATH", bad)
    provider_specs.load_provider_specs.cache_clear()
    with pytest.raises(ValueError, match="not valid JSON"):
        provider_specs.load_provider_specs()
    provider_specs.load_provider_specs.cache_clear()


def test_fail_fast_on_missing_field(monkeypatch: pytest.MonkeyPatch,
                                     tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"openai": {"base_url": "x"}}),
                   encoding="utf-8")
    monkeypatch.setattr(provider_specs, "_JSON_PATH", bad)
    provider_specs.load_provider_specs.cache_clear()
    with pytest.raises(ValueError, match="missing field"):
        provider_specs.load_provider_specs()
    provider_specs.load_provider_specs.cache_clear()


def test_hub_defaults_match_json() -> None:
    """The hub's probe defaults and the JSON agree — the mirror is honest."""
    from conscio.hub import providers as hub_providers
    for name, url in hub_providers._DEFAULT_BASE_URL.items():
        assert url == provider_specs.default_base_url(name)
