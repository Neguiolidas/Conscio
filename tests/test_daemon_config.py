# tests/test_daemon_config.py
"""v1.5.1: coverage for the daemon's config loader + adapter factories
(carried from the Hermes-Agent field run; previously untested).
config < env < CLI precedence is enforced in main(); these unit-test the parts.
"""
import json
from types import SimpleNamespace

import conscio.daemon as daemon


class TestLoadConfig:
    def test_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(daemon, "_CONFIG_PATHS", [tmp_path / "nope.json"])
        assert daemon._load_config() == {}

    def test_reads_first_existing(self, tmp_path, monkeypatch):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"model": "x", "interval": 9}))
        monkeypatch.setattr(daemon, "_CONFIG_PATHS", [tmp_path / "absent.json", p])
        cfg = daemon._load_config()
        assert cfg["model"] == "x" and cfg["interval"] == 9

    def test_bad_json_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "c.json"
        p.write_text("{ not json")
        monkeypatch.setattr(daemon, "_CONFIG_PATHS", [p])
        assert daemon._load_config() == {}

    def test_non_dict_json_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "c.json"
        p.write_text("[1, 2, 3]")
        monkeypatch.setattr(daemon, "_CONFIG_PATHS", [p])
        assert daemon._load_config() == {}


class TestBuildAdapterFromConfig:
    def test_no_adapter_block(self):
        a, t = daemon._build_adapter_from_config({}, fallback_model="m")
        assert a is None and t is None

    def test_adapter_without_type(self):
        a, t = daemon._build_adapter_from_config({"adapter": {}}, fallback_model="m")
        assert a is None and t is None

    def test_openai_block(self):
        a, t = daemon._build_adapter_from_config(
            {"adapter": {"type": "openai", "model": "gpt-4o", "api_key": "k"}},
            fallback_model="fallback")
        assert t == "openai"
        assert type(a).__name__ == "OpenAIAdapter"
        assert a.model == "gpt-4o"
        assert a.base_url == "https://api.openai.com/v1"

    def test_falls_back_to_model_when_unset(self):
        a, t = daemon._build_adapter_from_config(
            {"adapter": {"type": "ollama"}}, fallback_model="fb-model")
        assert t == "ollama" and a.model == "fb-model"

    def test_base_url_override(self):
        a, _ = daemon._build_adapter_from_config(
            {"adapter": {"type": "openai-compat", "base_url": "http://x:9/v1/"}},
            fallback_model="m")
        assert a.base_url == "http://x:9/v1"        # trailing slash stripped

    def test_unknown_type_ignored(self):
        a, t = daemon._build_adapter_from_config(
            {"adapter": {"type": "bogus"}}, fallback_model="m")
        assert a is None and t is None


class TestBuildAdapterFromCli:
    def _args(self, **kw):
        base = dict(adapter="openai", adapter_model=None, base_url=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_openai_uses_fallback_model(self):
        a = daemon._build_adapter_from_cli(self._args(), "fb")
        assert type(a).__name__ == "OpenAIAdapter" and a.model == "fb"

    def test_adapter_model_overrides_fallback(self):
        a = daemon._build_adapter_from_cli(
            self._args(adapter="ollama", adapter_model="llama3"), "fb")
        assert type(a).__name__ == "OllamaAdapter" and a.model == "llama3"

    def test_base_url_override(self):
        a = daemon._build_adapter_from_cli(
            self._args(adapter="openai-compat", base_url="http://x/v1"), "fb")
        assert a.base_url == "http://x/v1"
