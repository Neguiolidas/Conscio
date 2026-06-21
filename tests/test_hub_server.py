# tests/test_hub_server.py
import pytest
from conscio.hub import server


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


import conscio.adapter_config as ac
import json as _json


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
