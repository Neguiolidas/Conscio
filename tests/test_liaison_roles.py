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