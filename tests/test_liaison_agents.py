# tests/test_liaison_agents.py
"""Tests for conscio.liaison.agents — presence + capabilities + heartbeat."""
import time

import pytest

from conscio.liaison import agents

# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """A liaison.db with the agents table ready (mailbox.connect creates it)."""
    return tmp_path / "liaison.db"


def _now() -> float:
    return time.time()


# ── Schema & basic CRUD ────────────────────────────────────────────────

class TestRegister:
    def test_register_new_agent(self, db):
        assert agents.register_agent(db, instance_id="agent-a", model="gpt-x",
                                     capabilities=("code", "review"))
        row = agents.get_agent(db, "agent-a")
        assert row is not None
        assert row["model"] == "gpt-x"
        assert sorted(row["capabilities"]) == ["code", "review"]
        assert row["status"] == "alive"
        assert isinstance(row["last_heartbeat"], float)

    def test_register_idempotent_upsert(self, db):
        agents.register_agent(db, instance_id="agent-a", model="m1",
                              capabilities=("code",))
        agents.register_agent(db, instance_id="agent-a", model="m2",
                              capabilities=("review",))
        row = agents.get_agent(db, "agent-a")
        # UPSERT replaces ALL fields with the latest registration
        assert row["model"] == "m2"
        assert row["capabilities"] == ["review"]

    def test_register_empty_instance_id_is_noop(self, db):
        assert agents.register_agent(db, instance_id="",
                                     capabilities=("code",)) is False
        assert agents.list_agents(db) == []

    def test_register_on_unwritable_db_returns_false(self, tmp_path):
        # A regular file used as a parent path prevents sqlite from opening.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        bad = blocker / "liaison.db"
        assert agents.register_agent(bad, instance_id="x") is False

    def test_list_on_unwritable_db_returns_empty(self, tmp_path):
        # A path that cannot be created — list returns [] (read-only safe).
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        bad = blocker / "liaison.db"
        assert agents.list_agents(bad) == []

    def test_capabilities_normalised_lowercased_deduped(self, db):
        agents.register_agent(db, instance_id="a", capabilities=(
            "Code", "code", "REVIEW", "review", "  chat  ",
        ))
        row = agents.get_agent(db, "a")
        assert row["capabilities"] == ["chat", "code", "review"]


class TestHeartbeat:
    def test_heartbeat_refreshes_timestamp(self, db):
        agents.register_agent(db, instance_id="a",
                              heartbeat=_now() - 1000.0)
        assert agents.heartbeat(db, "a")
        row = agents.get_agent(db, "a")
        assert (row["last_heartbeat"] - _now()) < 5.0   # within 5s

    def test_heartbeat_can_replace_capabilities(self, db):
        agents.register_agent(db, instance_id="a", capabilities=("code",))
        agents.heartbeat(db, "a", capabilities=("review", "chat"))
        assert sorted(agents.get_agent(db, "a")["capabilities"]) == [
            "chat", "review"]

    def test_heartbeat_unknown_agent_is_noop(self, db):
        assert agents.heartbeat(db, "ghost") is False
        assert agents.get_agent(db, "ghost") is None

    def test_heartbeat_only_updates_timestamp_when_no_args(self, db):
        agents.register_agent(db, instance_id="a", model="m1",
                              capabilities=("code",))
        old = agents.get_agent(db, "a")
        time.sleep(0.01)
        agents.heartbeat(db, "a")
        new = agents.get_agent(db, "a")
        assert new["model"] == old["model"]
        assert new["capabilities"] == old["capabilities"]
        assert new["last_heartbeat"] > old["last_heartbeat"]


class TestUnregister:
    def test_unregister_removes_row(self, db):
        agents.register_agent(db, instance_id="a")
        assert agents.unregister(db, "a") is True
        assert agents.get_agent(db, "a") is None

    def test_unregister_unknown_is_silent(self, db):
        assert agents.unregister(db, "ghost") is True   # silent no-op


# ── Discovery / filtering ─────────────────────────────────────────────

class TestListAgents:
    def test_list_returns_all(self, db):
        for i in range(3):
            agents.register_agent(db, instance_id=f"a-{i}",
                                  capabilities=("code",))
        assert len(agents.list_agents(db)) == 3

    def test_list_filters_by_capability(self, db):
        agents.register_agent(db, instance_id="coder", capabilities=("code",))
        agents.register_agent(db, instance_id="reviewer",
                              capabilities=("review",))
        agents.register_agent(db, instance_id="polyglot",
                              capabilities=("code", "review"))
        ids = [a["instance_id"] for a in agents.list_agents(db, capability="code")]
        assert sorted(ids) == ["coder", "polyglot"]

    def test_list_excludes_stale_by_default(self, db):
        agents.register_agent(db, instance_id="old",
                              heartbeat=_now() - 1000.0)
        agents.register_agent(db, instance_id="fresh")
        alive = [a["instance_id"] for a in agents.list_agents(
            db, include_stale=False, capability=None)]
        assert alive == ["fresh"]

    def test_list_includes_stale_when_requested(self, db):
        agents.register_agent(db, instance_id="old",
                              heartbeat=_now() - 1000.0)
        ids = [a["instance_id"] for a in agents.list_agents(
            db, include_stale=True)]
        assert "old" in ids

    def test_list_capability_is_case_insensitive(self, db):
        agents.register_agent(db, instance_id="c", capabilities=("Code",))
        assert [a["instance_id"] for a in agents.list_agents(
            db, capability="CODE")] == ["c"]


# ── liveness ──────────────────────────────────────────────────────────

class TestIsAlive:
    def test_alive_fresh(self, db):
        agents.register_agent(db, instance_id="a")
        assert agents.is_alive(db, "a") is True

    def test_not_alive_when_stale(self, db):
        agents.register_agent(db, instance_id="a",
                              heartbeat=_now() - 1000.0)
        assert agents.is_alive(db, "a") is False

    def test_not_alive_when_unknown(self, db):
        assert agents.is_alive(db, "ghost") is False

    def test_alive_respects_custom_window(self, db):
        agents.register_agent(db, instance_id="a",
                              heartbeat=_now() - 5.0)
        assert agents.is_alive(db, "a", stale_after=2.0) is False
        assert agents.is_alive(db, "a", stale_after=100.0) is True


# ── Resilience ────────────────────────────────────────────────────────

class TestNeverRaises:
    def test_get_on_missing_db_returns_none(self, tmp_path):
        assert agents.get_agent(tmp_path / "nope.db", "x") is None

    def test_list_on_missing_db_returns_empty(self, tmp_path):
        assert agents.list_agents(tmp_path / "nope.db") == []


# ── Identity columns (v4.5: nome/familia/runtime/papel) ───────────────

class TestIdentity:
    def test_register_with_identity_fields(self, db):
        agents.register_agent(db, instance_id="agent-id",
                              model="opus-5", nome="Claude",
                              familia="claude", runtime="claude-code/2.x",
                              papel="executor")
        row = agents.get_agent(db, "agent-id")
        assert row is not None
        assert row["nome"] == "Claude"
        assert row["familia"] == "claude"
        assert row["runtime"] == "claude-code/2.x"
        assert row["papel"] == "executor"

    def test_register_without_identity_defaults_empty(self, db):
        agents.register_agent(db, instance_id="old")
        row = agents.get_agent(db, "old")
        assert row is not None
        assert row["nome"] == ""
        assert row["familia"] == ""
        assert row["runtime"] == ""
        assert row["papel"] == ""

    def test_identity_survives_upsert(self, db):
        agents.register_agent(db, instance_id="a", model="m1", nome="A",
                              familia="f", runtime="r", papel="p")
        agents.register_agent(db, instance_id="a", model="m2")
        row = agents.get_agent(db, "a")
        assert row is not None
        # upsert sem identity preserva as anteriores? NO — v4.1.1 upsert
        # sobescreve TODOS os campos. Mantemos identidade: register com
        # identity definida sobescreve; sem identity, preserva (não zera).
        assert row["nome"] == "A"
        assert row["model"] == "m2"

    def test_identity_in_list(self, db):
        agents.register_agent(db, instance_id="a", nome="X",
                              familia="claude")
        items = agents.list_agents(db)
        assert items[0]["nome"] == "X"
        assert items[0]["familia"] == "claude"

    def test_heartbeat_preserves_identity(self, db):
        agents.register_agent(db, instance_id="a", nome="N", familia="F")
        agents.heartbeat(db, "a")
        row = agents.get_agent(db, "a")
        assert row is not None
        assert row["nome"] == "N"
        assert row["familia"] == "F"

    def test_identity_columns_exist_on_fresh_and_legacy(self, tmp_path):
        # db legado (criado sem identity) → _conn migra por ALTER
        import sqlite3 as _s
        legacy = tmp_path / "legacy.db"
        conn = _s.connect(str(legacy))
        conn.execute("CREATE TABLE agents ("
                     " instance_id TEXT PRIMARY KEY,"
                     " model TEXT NOT NULL DEFAULT '',"
                     " status TEXT NOT NULL DEFAULT 'alive',"
                     " capabilities TEXT NOT NULL DEFAULT '',"
                     " last_heartbeat REAL NOT NULL)")
        conn.commit(); conn.close()
        agents.register_agent(legacy, instance_id="a", nome="N")
        row = agents.get_agent(legacy, "a")
        assert row is not None
        assert row["nome"] == "N"
