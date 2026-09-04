# tests/test_liaison_roles.py
"""Tests for conscio.liaison.roles — executor/orchestrator role model.

Squad roles: many EXECUTORS + exactly ONE ORCHESTRATOR. The orchestrator is
the agent that started the relay/chat, and agents can hand off or seize the
role mid-conversation (a new orchestrator demotes the previous one).
Invariant: at most one row has papel == "orchestrator" at any time.
"""
import sqlite3

from conscio.liaison import agents, roles


def _db(tmp_path):
    return tmp_path / "liaison.db"


class TestValidRoles:
    def test_papels_defined(self):
        assert roles.EXECUTOR == "executor"
        assert roles.ORCHESTRATOR == "orchestrator"
        assert roles.VALID_PAPELS == {"executor", "orchestrator"}

    def test_normalize_empty_to_executor(self):
        assert roles.normalize("") == "executor"
        assert roles.normalize("EXECUTOR") == "executor"
        assert roles.normalize("  Orquestrador ") == "orchestrator"
        assert roles.normalize("weird") == "executor"   # unknown → default


class TestSetRole:
    def test_set_executor(self):
        import tempfile
        db = _db(__import__("pathlib").Path(tempfile.mkdtemp()))
        agents.register_agent(db, instance_id="a", papel="executor")
        assert roles.set_role(db, "a", "executor") is True
        assert roles.get_role(db, "a") == "executor"

    def test_set_orchestrator_demotes_previous(self):
        import tempfile
        db = _db(__import__("pathlib").Path(tempfile.mkdtemp()))
        agents.register_agent(db, instance_id="a", model="gemini")
        agents.register_agent(db, instance_id="b", model="gemini")
        roles.set_role(db, "a", "orchestrator")
        roles.set_role(db, "b", "orchestrator")   # b assume; a cai p/ executor
        assert roles.get_role(db, "b") == "orchestrator"
        assert roles.get_role(db, "a") == "executor"
        assert roles.who_is_orchestrator(db) == "b"

    def test_single_orchestrator_invariant(self):
        import tempfile
        db = _db(__import__("pathlib").Path(tempfile.mkdtemp()))
        for i in range(5):
            agents.register_agent(db, instance_id=f"m{i}")
        for i in range(5):
            roles.set_role(db, f"m{i}", "orchestrator")
        # só o último é orquestrador
        assert roles.who_is_orchestrator(db) == "m4"
        conn = sqlite3.connect(str(db))
        cnt = conn.execute("SELECT COUNT(*) FROM agents WHERE papel='orchestrator'").fetchone()[0]
        conn.close()
        assert cnt == 1


class TestUnknown:
    def test_get_role_unknown_agent(self):
        import tempfile
        db = _db(__import__("pathlib").Path(tempfile.mkdtemp()))
        assert roles.get_role(db, "ghost") == roles.EXECUTOR

    def test_set_role_missing_db_false(self, tmp_path):
        db = tmp_path / "nope.db"
        assert roles.set_role(db, "a", "orchestrator") is False

    def test_set_role_invalid_agent_false(self):
        import tempfile
        db = _db(__import__("pathlib").Path(tempfile.mkdtemp()))
        assert roles.set_role(db, "ghost", "orchestrator") is False

# ── v4.5.4 A6: a invariante deixa de morar num módulo sem chamador ────────

def test_register_agent_demotes_previous_orchestrator(tmp_path):
    from conscio.liaison import agents
    db = tmp_path / "a.db"
    agents.register_agent(db, instance_id="a", papel="orquestrador")
    agents.register_agent(db, instance_id="b", papel="orquestrador")
    assert roles.who_is_orchestrator(db) == "b"
    assert roles.get_role(db, "a") == roles.EXECUTOR


def test_register_agent_normalizes_papel(tmp_path):
    from conscio.liaison import agents
    db = tmp_path / "a.db"
    agents.register_agent(db, instance_id="a", papel="  ORQUESTRADOR ")
    assert roles.get_role(db, "a") == roles.ORCHESTRATOR
    assert agents.get_agent(db, "a")["papel"] == roles.ORCHESTRATOR


def test_new_agent_is_always_an_executor(tmp_path):
    """Regra do dono: todo mundo entra como executor; o papel muda depois."""
    from conscio.liaison import agents
    db = tmp_path / "a.db"
    agents.register_agent(db, instance_id="a")
    assert roles.get_role(db, "a") == roles.EXECUTOR


def test_heartbeat_does_not_silently_demote_the_orchestrator(tmp_path):
    """Re-registro sem papel preserva o papel — senão o líder perde o posto
    no próximo tick do próprio heartbeat."""
    from conscio.liaison import agents
    db = tmp_path / "a.db"
    agents.register_agent(db, instance_id="a", papel="orchestrator")
    agents.register_agent(db, instance_id="a")          # tick de presença
    assert roles.who_is_orchestrator(db) == "a"


def test_projection_resolves_rival_claims_by_card_freshness(tmp_path,
                                                            monkeypatch):
    """Two peers can each claim the role on their own machine. Projection
    demotes as it writes, so the winner used to be whoever the directory
    listed last (id order): 'zz' beat a claim made an hour later."""
    from conscio.liaison import agents, directory
    from conscio.mcp.server import Bindings

    stale = {"instance_id": "zz-stale", "papel": "orchestrator",
             "updated_at": 1000.0, "capabilities": ["relay"]}
    fresh = {"instance_id": "aa-fresh", "papel": "orchestrator",
             "updated_at": 9000.0, "capabilities": ["relay"]}
    # directory order is id order — the shape that produced the bug
    monkeypatch.setattr(directory, "peers", lambda **kw: [fresh, stale])
    monkeypatch.setattr(directory, "is_live", lambda card: True)

    b = Bindings.__new__(Bindings)                 # projection needs no engine
    b.self_instance_id = "me"
    b.liaison_db = tmp_path / "l.db"
    agents.register_agent(b.liaison_db, instance_id="me")
    b._sync_directory_registry()

    assert roles.who_is_orchestrator(b.liaison_db) == "aa-fresh"
