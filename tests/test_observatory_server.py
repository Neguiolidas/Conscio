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
    def daemon(self): return {"running": True, "awake": True, "cycles": 7}
    def identity(self): return {"instance_id": "me-123", "label": "host-me"}


P = FakeProjection()


class FakeSociety:
    db = "/fake/noosphere.db"                   # real SocietyProjection exposes .db
    def members(self): return [{"origin_instance_id": "i", "origin_label": "L"}]
    def skills(self, **k): return [{"origin_label": "L", "goal_fp": "fp"}]
    def records(self, **k): return [{"origin_label": "L", "entry_count": 3}]


S = FakeSociety()


class FakeLiaison:
    db = "/fake/liaison.db"                      # real LiaisonProjection exposes .db
    def inbox(self, self_id, **k):
        return [{"id": 9, "from_instance": "peer", "payload": {"text": "hi"}}]


L = FakeLiaison()


def _route(method, path, query=None, *, token=None, auth=None):
    return srv.route(method, path, query or {}, projection=P, society=S,
                     liaison=L, token=token, auth=auth)


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


def test_society_endpoints_return_projection_shape():
    assert _route("GET", "/api/society/members").payload == \
        [{"origin_instance_id": "i", "origin_label": "L"}]
    assert _route("GET", "/api/society/skills").payload == \
        [{"origin_label": "L", "goal_fp": "fp"}]
    assert _route("GET", "/api/society/records").payload == \
        [{"origin_label": "L", "entry_count": 3}]


def test_society_mutation_verbs_405():
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert _route(m, "/api/society/members").status == 405


def test_health_includes_noosphere():
    assert _route("GET", "/api/health").payload["noosphere"] == "/fake/noosphere.db"


def test_daemon_and_identity_routes():
    assert _route("GET", "/api/daemon").payload == {"running": True,
                                                    "awake": True, "cycles": 7}
    assert _route("GET", "/api/identity").payload["instance_id"] == "me-123"


def test_relay_inbox_route_resolves_self_from_identity():
    r = _route("GET", "/api/relay/inbox")
    assert r.status == 200
    assert r.payload[0]["from_instance"] == "peer"


def test_new_read_routes_405_on_mutation():
    for path in ("/api/daemon", "/api/identity", "/api/relay/inbox"):
        for m in ("POST", "PUT", "PATCH", "DELETE"):
            assert _route(m, path).status == 405


def test_health_includes_liaison():
    assert _route("GET", "/api/health").payload["liaison"] == "/fake/liaison.db"


def test_head_accepted_not_501():
    # v2.4 deferred fix folded in: route() accepts HEAD; Handler.do_HEAD wires it.
    assert _route("HEAD", "/api/events").status == 200
    assert _route("HEAD", "/").status == 200
