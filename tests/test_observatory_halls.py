# tests/test_observatory_halls.py
"""Tests for conscio.observatory.halls_view — read-only projection of agents + halls."""
import time

from conscio.liaison import agents, halls, mailbox
from conscio.observatory.halls_view import HallsProjection


def _db(tmp_path):
    return tmp_path / "liaison.db"


def _seed(db):
    """agents + halls + mailbox, read-only-safe."""
    agents.register_agent(db, instance_id="a1", model="opus-5",
                          familia="claude", capabilities=("code", "review"))
    agents.register_agent(db, instance_id="a2", model="gemini-2.5",
                          familia="gemini", capabilities=("chat",))
    agents.register_agent(db, instance_id="stale", model="old",
                          heartbeat=time.time() - 1000.0)
    h = halls.create_hall(db, dono="a1", nome="Squad QA")
    assert h is not None
    halls.add_member(db, hall_id=h["hall_id"], instance_id="a1", papel="dono")
    halls.add_member(db, hall_id=h["hall_id"], instance_id="a2",
                     papel="executor")
    mailbox.send(db, from_instance="a2", to_instance="a1", type="chat",
                 payload={"text": "oi"})
    return h["hall_id"]


class TestAgents:
    def test_agents_lists_live_and_marks_offline(self, tmp_path):
        db = _db(tmp_path); _seed(db)
        p = HallsProjection(db)
        live = p.agents()   # stale excluídos por default
        ids = {a["instance_id"] for a in live}
        assert "a1" in ids and "a2" in ids and "stale" not in ids
        assert not any(a.get("offline") for a in live)

    def test_agents_include_stale_shows_offline(self, tmp_path):
        db = _db(tmp_path); _seed(db)
        p = HallsProjection(db)
        all_rows = p.agents(include_stale=True)
        stale = [a for a in all_rows if a["instance_id"] == "stale"]
        assert stale and stale[0]["offline"] is True

    def test_agents_capabilities_parsed(self, tmp_path):
        db = _db(tmp_path); _seed(db)
        p = HallsProjection(db)
        a1 = next(a for a in p.agents() if a["instance_id"] == "a1")
        assert "code" in a1["capabilities"] and "review" in a1["capabilities"]


class TestHalls:
    def test_halls_with_member_count(self, tmp_path):
        db = _db(tmp_path); hid = _seed(db)
        p = HallsProjection(db)
        hs = p.halls()
        assert len(hs) == 1
        assert hs[0]["hall_id"] == hid
        assert hs[0]["member_count"] == 2

    def test_halls_filter_by_dono(self, tmp_path):
        db = _db(tmp_path); _seed(db)
        p = HallsProjection(db)
        assert len(p.halls(dono="a1")) == 1
        assert p.halls(dono="ghost") == []

    def test_hall_members_with_identity(self, tmp_path):
        db = _db(tmp_path); hid = _seed(db)
        p = HallsProjection(db)
        members = p.hall_members(hid)
        m = {x["instance_id"]: x for x in members}
        assert m["a1"]["modelo"] == "opus-5"
        assert m["a2"]["familia"] == "gemini"
        assert "stale" not in m   # não está no hall

    def test_hall_members_alive_only_excludes_stale(self, tmp_path):
        db = _db(tmp_path); hid = _seed(db)
        # adiciona membro stale no hall
        halls.add_member(db, hall_id=hid, instance_id="stale")
        p = HallsProjection(db)
        alive = p.hall_members(hid, alive_only=True)
        assert all(not x["offline"] for x in alive)
        allm = p.hall_members(hid, alive_only=False)
        ids = {x["instance_id"] for x in allm}
        assert "stale" in ids


class TestMailboxes:
    def test_mailboxes_unread_by_peer(self, tmp_path):
        db = _db(tmp_path); _seed(db)
        p = HallsProjection(db)
        mb = p.mailboxes("a1")
        assert len(mb) == 1
        assert mb[0]["from_instance"] == "a2"
        assert mb[0]["unread"] == 1


class TestReadOnly:
    def test_no_write_on_read(self, tmp_path):
        db = _db(tmp_path); _seed(db)
        p = HallsProjection(db)
        before = db.stat().st_size
        p.agents(); p.halls(); p.mailboxes("a1")
        assert db.stat().st_size == before   # nunca cresce (só SELECT)

    def test_degrades_on_missing_db(self, tmp_path):
        p = HallsProjection(tmp_path / "nope.db")
        assert p.agents() == []
        assert p.halls() == []
        assert p.hall_members("x") == []
        assert p.mailboxes("a") == []