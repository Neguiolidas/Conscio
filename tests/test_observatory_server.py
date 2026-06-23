# tests/test_observatory_server.py
import pytest

from conscio.observatory import server as srv


class FakeProjection:
    storage = "/fake/storage"                  # real Projection exposes .storage
    def events(self, **k): return [{"id": 1, "type": "reflection"}]
    def actions(self, **k): return [{"id": 1, "tool": "fs_read"}]
    def skills(self, **k): return [{"id": 1, "goal_fp": "fp"}]
    def goals(self): return [{"description": "x"}]
    def state(self): return {"awake": True}


P = FakeProjection()


def _route(method, path, query=None, *, token=None, auth=None):
    return srv.route(method, path, query or {}, projection=P, token=token, auth=auth)


def test_check_host_refuses_non_loopback():
    srv._check_host("127.0.0.1")          # ok
    srv._check_host("localhost")          # ok
    with pytest.raises(ValueError):
        srv._check_host("0.0.0.0")
    with pytest.raises(ValueError):
        srv._check_host("8.8.8.8")


def test_api_endpoints_return_projection_shape():
    assert _route("GET", "/api/events").payload == [{"id": 1, "type": "reflection"}]
    assert _route("GET", "/api/actions").payload == [{"id": 1, "tool": "fs_read"}]
    assert _route("GET", "/api/skills").payload == [{"id": 1, "goal_fp": "fp"}]
    assert _route("GET", "/api/goals").payload == [{"description": "x"}]
    assert _route("GET", "/api/state").payload == {"awake": True}
    assert _route("GET", "/api/health").status == 200


def test_token_gate():
    assert _route("GET", "/api/events", token="s3cret").status == 401
    assert _route("GET", "/api/events", token="s3cret", auth="Bearer nope").status == 401
    assert _route("GET", "/api/events", token="s3cret", auth="Bearer s3cret").status == 200


def test_mutation_verbs_405():
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert _route(m, "/api/events").status == 405
        assert _route(m, "/").status == 405


def test_static_whitelist_and_no_traversal():
    assert _route("GET", "/").status == 200
    assert _route("GET", "/static/app.js").status == 200
    assert _route("GET", "/static/secret").status == 404
    assert _route("GET", "/static/../server.py").status == 404


def test_unknown_path_404():
    assert _route("GET", "/api/nope").status == 404
