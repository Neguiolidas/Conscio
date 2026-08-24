# tests/test_liaison_a2a.py
"""Tests for conscio.liaison.a2a — capability routing + ACK wire format."""
import pytest

from conscio.liaison import a2a, agents, mailbox


@pytest.fixture
def db(tmp_path):
    return tmp_path / "liaison.db"


# ── candidate selection ──────────────────────────────────────────────

class TestCandidatesByCapability:
    def test_returns_alive_agents_with_capability(self, db):
        agents.register_agent(db, instance_id="coder",
                              capabilities=("code",))
        agents.register_agent(db, instance_id="reviewer",
                              capabilities=("review",))
        agents.register_agent(db, instance_id="poly",
                              capabilities=("code", "review"))
        assert sorted(a2a.candidates_by_capability(db, "code")) == [
            "coder", "poly"]

    def test_empty_capability_is_empty(self, db):
        agents.register_agent(db, instance_id="a")
        assert a2a.candidates_by_capability(db, "") == []

    def test_stale_excluded(self, db):
        import time
        agents.register_agent(db, instance_id="old",
                              capabilities=("code",),
                              heartbeat=time.time() - 1000.0)
        agents.register_agent(db, instance_id="fresh",
                              capabilities=("code",))
        assert a2a.candidates_by_capability(db, "code") == ["fresh"]

    def test_missing_db_is_empty(self, tmp_path):
        assert a2a.candidates_by_capability(tmp_path / "nope.db", "code") == []


# ── deterministic selection ───────────────────────────────────────────

class TestRouteSelect:
    def test_picks_smallest_alive_capable(self, db):
        agents.register_agent(db, instance_id="zulu",
                              capabilities=("code",))
        agents.register_agent(db, instance_id="alpha",
                              capabilities=("code",))
        assert a2a.route_select(db, "code") == "alpha"   # smallest

    def test_prefer_wins_when_capable_and_alive(self, db):
        agents.register_agent(db, instance_id="alpha",
                              capabilities=("code",))
        agents.register_agent(db, instance_id="chosen",
                              capabilities=("code", "review"))
        assert a2a.route_select(db, "review", prefer="chosen") == "chosen"

    def test_prefer_ignored_when_without_capability(self, db):
        agents.register_agent(db, instance_id="alpha",
                              capabilities=("code",))
        agents.register_agent(db, instance_id="chosen",
                              capabilities=("review",))
        # chosen doesn't carry 'code' -> falls back to smallest capable
        assert a2a.route_select(db, "code", prefer="chosen") == "alpha"

    def test_no_candidate_returns_empty(self, db):
        agents.register_agent(db, instance_id="a",
                              capabilities=("review",))
        assert a2a.route_select(db, "code") == ""

    def test_missing_db_returns_empty(self, tmp_path):
        assert a2a.route_select(tmp_path / "nope.db", "code") == ""


# ── route + send (integration with mailbox) ──────────────────────────

class TestRouteAndSend:
    def test_sends_to_resolved_peer(self, db):
        agents.register_agent(db, instance_id="coder",
                              capabilities=("code",))
        agents.register_agent(db, instance_id="person",
                              capabilities=("chat",))
        mid = a2a.route_and_send(db, from_instance="self-1",
                                 capability="code", type="chat",
                                 payload={"text": "bo"})
        assert mid > 0
        box = mailbox.inbox(db, "coder", unread_only=True)
        assert len(box) == 1
        assert box[0]["payload"]["text"] == "bo"

    def test_no_capable_peer_returns_zero(self, db):
        agents.register_agent(db, instance_id="person",
                              capabilities=("chat",))
        assert a2a.route_and_send(db, from_instance="self-1",
                                  capability="code", type="chat",
                                  payload={"text": "x"}) == 0

    def test_route_and_send_respects_prefer(self, db):
        agents.register_agent(db, instance_id="a1",
                              capabilities=("code",))
        agents.register_agent(db, instance_id="fav",
                              capabilities=("code", "chat"))
        mid = a2a.route_and_send(db, from_instance="self-1", capability="chat",
                                 prefer="fav", type="chat",
                                 payload={"text": "hi"})
        assert mid > 0
        assert len(mailbox.inbox(db, "fav", unread_only=True)) == 1


# ── ACK wire format ──────────────────────────────────────────────────

class TestDeltaAck:
    def test_ack_payload_shape(self):
        ack = a2a.delta_ack_for("peer-1", 42)
        assert ack == {"ack": {"to": "peer-1", "since_id": 42}}

    def test_ack_replaces_late_since(self):
        assert a2a.delta_ack_for("p", 0)["ack"]["since_id"] == 0