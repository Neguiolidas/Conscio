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
