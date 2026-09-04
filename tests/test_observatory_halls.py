# tests/test_observatory_halls.py
"""Tests for conscio.observatory.halls_view — read-only projection of agents + halls.

O registro de agentes continua no liaison.db privado; o roster do hall mora no
diretório público (v4.5.4), então o seed monta cartão, não linha de tabela.
"""
import time

import pytest

from conscio.liaison import agents, directory, halls, mailbox
from conscio.observatory.halls_view import HallsProjection


@pytest.fixture(autouse=True)
def _relay_root(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))


def _db(tmp_path):
    return tmp_path / "liaison.db"


def _card(cid, **kw):
    directory.publish({"instance_id": cid, "spool": cid, "url": "",
                       "updated_at": time.time(), **kw})


def _seed(db):
    """agents (db privado) + halls (diretório) + mailbox, read-only-safe."""
    agents.register_agent(db, instance_id="a1", model="opus-5",
                          familia="claude", capabilities=("code", "review"))
    agents.register_agent(db, instance_id="a2", model="gemini-2.5",
                          familia="gemini", capabilities=("chat",))
    agents.register_agent(db, instance_id="stale", model="old",
                          heartbeat=time.time() - 1000.0)
    _card("a1", modelo="opus-5", familia="claude")
    _card("a2", modelo="gemini-2.5", familia="gemini")
    _card("stale", modelo="old")
    h = halls.create_hall(owner="a1", name="Squad QA")
    assert h is not None
    halls.join(instance_id="a2", hall_id=h["hall_id"])
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

    def test_agents_on_legacy_db_no_crash(self, tmp_path):
        # REGRESSÃO: agents() delega a list_agents (probe PRAGMA) e NÃO pode
        # hardcodar SELECT com colunas ausentes em db legado.
        import sqlite3 as _s
        import time as _t
        db = tmp_path / "legacy.db"
        c = _s.connect(str(db))
        c.execute("CREATE TABLE agents (instance_id TEXT PRIMARY KEY,"
                  " model TEXT NOT NULL DEFAULT '', status TEXT NOT NULL"
                  " DEFAULT 'alive', capabilities TEXT NOT NULL DEFAULT '',"
                  " last_heartbeat REAL NOT NULL)")
        c.execute("INSERT INTO agents VALUES ('a1','gemini','alive','relay',?)",
                  (_t.time(),))
        c.commit(); c.close()
        p = HallsProjection(db)
        rows = p.agents(include_stale=True)
        assert len(rows) == 1
        assert rows[0]["instance_id"] == "a1"
        assert rows[0]["familia"] == ""   # ausente vira '' por _identity_select

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

    def test_member_count_takes_one_directory_sweep(self, tmp_path,
                                                    monkeypatch):
        db = _db(tmp_path); _seed(db)
        halls.create_hall(owner="a1", name="Squad B")
        halls.create_hall(owner="a1", name="Squad C")
        calls = []
        real = directory.peers
        monkeypatch.setattr(directory, "peers",
                            lambda *a, **k: (calls.append(1), real(*a, **k))[1])
        hs = HallsProjection(db).halls()
        assert len(hs) == 3
        assert len(calls) == 1        # uma varredura para todos os halls

    def test_halls_filter_by_owner(self, tmp_path):
        db = _db(tmp_path); _seed(db)
        p = HallsProjection(db)
        assert len(p.halls(owner="a1")) == 1
        assert p.halls(owner="ghost") == []

    def test_hall_members_with_identity(self, tmp_path):
        db = _db(tmp_path); hid = _seed(db)
        p = HallsProjection(db)
        members = p.hall_members(hid)
        m = {x["instance_id"]: x for x in members}
        assert m["a1"]["model"] == "opus-5"
        assert m["a2"]["family"] == "gemini"
        assert m["a1"]["function"] == "leader"    # o criador nasce líder
        assert m["a2"]["function"] == "executor"
        assert "stale" not in m   # não está no hall

    def test_hall_members_alive_only_excludes_stale(self, tmp_path):
        db = _db(tmp_path); hid = _seed(db)
        halls.join(instance_id="stale", hall_id=hid)
        stale = directory.get("stale")             # envelhece o cartão
        stale["updated_at"] = time.time() - 10 * 3600
        directory.publish(stale)
        p = HallsProjection(db)
        alive = p.hall_members(hid, alive_only=True)
        assert all(not x["offline"] for x in alive)
        assert "stale" not in {x["instance_id"] for x in alive}
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