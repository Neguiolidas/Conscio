# tests/test_hub_server.py
import json as _json

import pytest

import conscio.adapter_config as ac
from conscio.hub import model_test, providers, server


def test_parse_json_body_ok():
    assert server.parse_json_body(b'{"a": 1}') == {"a": 1}


def test_parse_json_body_oversize():
    with pytest.raises(ValueError):
        server.parse_json_body(b"{}", max_bytes=1)


def test_parse_json_body_not_object():
    with pytest.raises(ValueError):
        server.parse_json_body(b"[1,2]")


def test_route_health():
    r = server.route("GET", "/api/health", {}, None, token=None, auth=None)
    assert r.status == 200 and r.payload["ok"] is True
    assert "version" in r.payload


def test_get_config_redacts(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(_json.dumps(
        {"model": "m", "adapter": {"type": "openai", "api_key": "leak"}}))
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("GET", "/api/config", {}, None, token=None, auth=None)
    assert r.status == 200 and "api_key" not in r.payload["adapter"]


def test_get_providers_lists_builtin(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("GET", "/api/providers", {}, None, token=None, auth=None)
    assert r.status == 200 and "openai" in r.payload["builtin"]


def test_put_config_writes(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    body = {"model": "glm-5.1", "adapter": {"type": "openai"}}
    r = server.route("PUT", "/api/config", {}, body, token=None, auth=None)
    assert r.status == 200
    assert _json.loads(p.read_text())["model"] == "glm-5.1"


def test_put_config_invalid_400(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    body = {"model": "m", "adapter": {"type": "bogus"}}    # valid model, bad type
    r = server.route("PUT", "/api/config", {}, body, token=None, auth=None)
    assert r.status == 400 and not p.exists()


def test_put_config_resolves_builtin_provider(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("PUT", "/api/config", {},
                     {"model": "llama3.1", "provider": "ollama"},
                     token=None, auth=None)
    assert r.status == 200
    a = _json.loads(p.read_text())["adapter"]
    assert a["type"] == "ollama" and a["base_url"] == "http://localhost:11434"


def test_put_config_resolves_custom_provider(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(_json.dumps(
        {"model": "m", "adapter": {"type": "openai"},
         "providers": {"logfare": {"type": "openai-compat",
                                   "base_url": "https://x/v1",
                                   "api_key_env": "LOGFARE_KEY"}}}))
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("PUT", "/api/config", {},
                     {"model": "glm-5.1", "provider": "logfare"},
                     token=None, auth=None)
    assert r.status == 200
    a = _json.loads(p.read_text())["adapter"]
    assert a["type"] == "openai-compat" and a["api_key_env"] == "LOGFARE_KEY"


def test_post_provider_adds(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(_json.dumps({"model": "m", "adapter": {"type": "openai"}}))
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    body = {"name": "logfare", "type": "openai-compat",
            "base_url": "https://x/v1", "api_key_env": "LOGFARE_KEY"}
    r = server.route("POST", "/api/providers", {}, body, token=None, auth=None)
    assert r.status == 200
    assert "logfare" in _json.loads(p.read_text())["providers"]


def test_post_provider_rejects_raw_key(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(_json.dumps({"model": "m", "adapter": {"type": "openai"}}))
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    body = {"name": "x", "type": "openai", "api_key_env": "sk-raw-key"}
    r = server.route("POST", "/api/providers", {}, body, token=None, auth=None)
    assert r.status == 400


def test_get_models_resolves_and_probes(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    monkeypatch.setattr(providers, "probe_models",
                        lambda pc, **k: {"models": ["m1"], "source": "api",
                                         "probed": True})
    r = server.route("GET", "/api/models", {"provider": "ollama"}, None,
                     token=None, auth=None)
    assert r.status == 200 and r.payload["models"] == ["m1"]


def test_get_models_unknown_provider_404(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("GET", "/api/models", {"provider": "nope"}, None,
                     token=None, auth=None)
    assert r.status == 404


def test_get_models_missing_param_400(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("GET", "/api/models", {}, None, token=None, auth=None)
    assert r.status == 400


def test_post_model_test(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    monkeypatch.setattr(model_test, "smoke_test",
                        lambda pc, model, **k: {"ok": True, "latency_ms": 5,
                                                "sample_output": "OK", "model": model})
    body = {"provider": "ollama", "model": "llama3.1"}
    r = server.route("POST", "/api/model/test", {}, body, token=None, auth=None)
    assert r.status == 200 and r.payload["ok"] is True


def test_token_gate_blocks_without_auth():
    r = server.route("GET", "/api/config", {}, None, token="secret", auth=None)
    assert r.status == 401


def test_token_gate_allows_with_auth(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("GET", "/api/config", {}, None,
                     token="secret", auth="Bearer secret")
    assert r.status == 200


def test_static_index_served():
    r = server.route("GET", "/", {}, None, token=None, auth=None)
    assert r.status == 200 and r.content_type == "text/html"


def test_static_traversal_blocked():
    r = server.route("GET", "/static/../config.py", {}, None,
                     token=None, auth=None)
    assert r.status == 404


def test_post_provider_rejects_raw_api_key_field(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(_json.dumps({"model": "m", "adapter": {"type": "openai"}}))
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    body = {"name": "x", "type": "openai", "api_key": "sk-live-xxx"}
    r = server.route("POST", "/api/providers", {}, body, token=None, auth=None)
    assert r.status == 400


def test_get_config_redacts_providers(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(_json.dumps(
        {"model": "m", "adapter": {"type": "openai"},
         "providers": {"x": {"type": "openai", "api_key": "leak"}}}))
    monkeypatch.setattr(ac, "_CONFIG_PATHS", [p])
    r = server.route("GET", "/api/config", {}, None, token=None, auth=None)
    assert "api_key" not in r.payload["providers"]["x"]
