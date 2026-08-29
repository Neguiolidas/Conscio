# tests/test_liaison_halls.py
"""Tests for conscio.liaison.halls — Agent's Hall: named groups over mailbox."""
import time

import pytest

from conscio.liaison import agents, halls, mailbox


@pytest.fixture
def db(tmp_path):
    return tmp_path / "liaison.db"


def _now():
    return time.time()


class TestCreateHall:
    def test_create_hall_generates_id_and_owner(self, db):
        h = halls.create_hall(db, dono="agent-a", nome="Squad QA")
        assert h is not None
        assert h["dono"] == "agent-a"
        assert h["hall_id"] == "agent-a--squad-qa"
        assert isinstance(h["criado_em"], float)

    def test_create_hall_id_normalized(self, db):
        h = halls.create_hall(db, dono="agent-a", nome="  Team  Alpha!  ")
        assert h["hall_id"] == "agent-a--team-alpha"

    def test_create_duplicate_returns_none(self, db):
        assert halls.create_hall(db, dono="a", nome="team") is not None
        assert halls.create_hall(db, dono="a", nome="team") is None

    def test_create_on_broken_db_returns_none(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        assert halls.create_hall(blocker / "x.db", dono="a", nome="t") is None


class TestMembership:
    def test_add_remove_member_roundtrip(self, db):
        h = halls.create_hall(db, dono="d", nome="hall")
        assert halls.add_member(db, hall_id=h["hall_id"], instance_id="m")
        assert halls.is_member(db, hall_id=h["hall_id"], instance_id="m")
        assert halls.remove_member(db, hall_id=h["hall_id"], instance_id="m")
        assert not halls.is_member(db, hall_id=h["hall_id"], instance_id="m")

    def test_members_of_lists_with_paper(self, db):
        h = halls.create_hall(db, dono="d", nome="hall")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="m",
                         papel="executor")
        members = halls.members_of(db, h["hall_id"])
        assert len(members) == 1
        assert members[0]["instance_id"] == "m"
        assert members[0]["papel"] == "executor"

    def test_halls_of_returns_memberships(self, db):
        h1 = halls.create_hall(db, dono="a", nome="h1")
        h2 = halls.create_hall(db, dono="b", nome="h2")
        halls.add_member(db, hall_id=h1["hall_id"], instance_id="me")
        halls.add_member(db, hall_id=h2["hall_id"], instance_id="me")
        mine = halls.halls_of(db, "me")
        assert {x["hall_id"] for x in mine} == {h1["hall_id"], h2["hall_id"]}


class TestPresenceAware:
    def test_members_alive_only_filters_stale(self, db):
        h = halls.create_hall(db, dono="d", nome="hall")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="fresh")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="stale")
        agents.register_agent(db, instance_id="fresh", capabilities=("relay",))
        agents.register_agent(db, instance_id="stale", capabilities=("relay",),
                              heartbeat=_now() - 1000.0)
        alive = halls.members_of(db, h["hall_id"], alive_only=True)
        ids = {m["instance_id"] for m in alive}
        assert "fresh" in ids and "stale" not in ids

    def test_members_alive_only_all_when_no_registry(self, db):
        h = halls.create_hall(db, dono="d", nome="hall")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="m")
        # sem registro → alive_only=True não consegue filtrar: inclui todos
        assert len(halls.members_of(db, h["hall_id"], alive_only=True)) == 1


class TestSendToHall:
    def test_fanout_excludes_sender(self, db):
        h = halls.create_hall(db, dono="d", nome="hall")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="d")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="m1")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="m2")
        n = halls.send_to_hall(db, from_instance="d", hall_id=h["hall_id"],
                               type="chat", payload={"text": "oi"})
        assert n == 2                      # m1, m2 (d excluído)
        assert len(mailbox.inbox(db, "m1")) == 1
        assert len(mailbox.inbox(db, "m2")) == 1
        assert mailbox.inbox(db, "d") == []  # remetente não recebeu

    def test_fanout_isolates_failure(self, db):
        h = halls.create_hall(db, dono="d", nome="hall")
        halls.add_member(db, hall_id=h["hall_id"], instance_id="keep")
        n = halls.send_to_hall(db, from_instance="d", hall_id=h["hall_id"],
                               type="chat", payload={"text": "x"})
        assert n == 1


class TestNeverRaises:
    def test_all_degrades_on_unwritable_db(self, tmp_path):
        # caminho cujo pai é um arquivo → sqlite não abre, degrada (NÃO cria)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        no = blocker / "nope.db"
        assert halls.create_hall(no, dono="a", nome="t") is None
        assert halls.get_hall(no, "x") is None
        assert halls.list_halls(no) == []
        assert halls.members_of(no, "x") == []
        assert halls.halls_of(no, "a") == []
        assert halls.send_to_hall(no, from_instance="a", hall_id="x",
                                  type="chat", payload={}) == 0
        assert halls.is_member(no, "x", "a") is False