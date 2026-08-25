# tests/test_liaison_tick.py
"""Tests for conscio.liaison.tick — private-cursor relay sweep."""
import json
import os
import sqlite3
import time
from pathlib import Path

from conscio.liaison import tick as tickmod


class TestClassify:
    def test_important_direcionamento(self):
        assert tickmod.classify_important({"tipo": "direcionamento"}) is True

    def test_important_status_uppercase(self):
        assert tickmod.classify_important(
            {"status": "DIRECIONAMENTO_PROXIMO_PASSO"}) is True

    def test_important_teste_direcionado(self):
        assert tickmod.classify_important(
            {"tipo": "teste_direcionado"}) is True

    def test_important_acao(self):
        assert tickmod.classify_important({"tipo": "acao"}) is True

    def test_routine_ping(self):
        assert tickmod.classify_important({"tipo": "ping_teste_despertar"}) is False

    def test_routine_validacao(self):
        assert tickmod.classify_important(
            {"tipo": "validacao_multi_identity_watcher"}) is False

    def test_routine_ack(self):
        assert tickmod.classify_important({"tipo": "ack_ping"}) is False

    def test_payload_json_string(self):
        assert tickmod.classify_important(
            '{"tipo": "direcionamento"}') is True

    def test_non_dict_payload(self):
        # classify_important only inspects dict payloads; other types → False.
        assert tickmod.classify_important({}) is False

    def test_empty_payload(self):
        assert tickmod.classify_important({}) is False


class TestClassifyPeer:
    def test_short_form(self):
        assert tickmod.classify_peer(
            "3c8c0259-fa34-4294-9d41-00bb8bb6741e") == "3c8c0259"

    def test_fallback_token(self):
        assert tickmod.classify_peer("gemini") == "gemini"


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "liaison.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_instance TEXT,
            to_instance TEXT,
            type TEXT,
            payload TEXT,
            ts REAL
        )
    """)
    conn.commit()
    conn.close()
    return db


def _insert(db: Path, from_i: str, to_i: str, payload: dict,
            mtype: str = "chat") -> int:
    conn = sqlite3.connect(str(db))
    cur = conn.execute(
        "INSERT INTO messages (from_instance, to_instance, type, payload, ts)"
        " VALUES (?,?,?,?,?)",
        (from_i, to_i, mtype, json.dumps(payload), time.time()),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return int(pid) if pid is not None else 0


class TestSweep:
    def test_no_cursor_returns_all(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, "peer1", "self", {"tipo": "direcionamento"})
        msgs = tickmod.sweep(db, "self", ["peer1"], None)
        assert len(msgs) == 1
        assert msgs[0]["payload"]["tipo"] == "direcionamento"

    def test_private_cursor_filters(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, "peer1", "self", {"tipo": "ping"})
        _insert(db, "peer1", "self", {"tipo": "direcionamento"})
        cursor = tmp_path / "cursor.txt"
        cursor.write_text("1")
        msgs = tickmod.sweep(db, "self", ["peer1"], cursor)
        assert len(msgs) == 1
        assert msgs[0]["payload"]["tipo"] == "direcionamento"

    def test_missing_db_returns_empty(self, tmp_path):
        msgs = tickmod.sweep(tmp_path / "nope.db", "self", ["peer1"], None)
        assert msgs == []

    def test_wrong_self_address(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, "peer1", "other", {"tipo": "ping"})
        msgs = tickmod.sweep(db, "self", ["peer1"], None)
        assert msgs == []

    def test_own_message_skipped(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, "self", "self", {"tipo": "ping"})
        msgs = tickmod.sweep(db, "self", ["peer1"], None)
        assert msgs == []

    def test_untracked_peer_ignored(self, tmp_path):
        db = _make_db(tmp_path)
        _insert(db, "notpeer", "self", {"tipo": "ping"})
        msgs = tickmod.sweep(db, "self", ["peer1"], None)
        assert msgs == []


class TestAdvanceCursor:
    def test_writes_max_id(self, tmp_path):
        cursor = tmp_path / "sub" / "cursor.txt"
        tickmod.advance_private_cursor(cursor, [5, 9, 3])
        assert cursor.read_text() == "9"

    def test_empty_noop(self, tmp_path):
        cursor = tmp_path / "c.txt"
        tickmod.advance_private_cursor(cursor, [])
        assert not cursor.exists()


class TestCLI:
    def test_sweep_cli_json(self, tmp_path, capsys):
        db = _make_db(tmp_path)
        _insert(db, "peer1", "self", {"tipo": "direcionamento"})
        rc = tickmod.main([
            "--liaison-db", str(db),
            "--self-id", "self",
            "--relay-peer", "peer1",
            "--json",
        ])
        assert rc == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert data["messages"][0]["payload"]["tipo"] == "direcionamento"

    def test_silent_when_empty(self, tmp_path, capsys):
        db = _make_db(tmp_path)
        rc = tickmod.main([
            "--liaison-db", str(db),
            "--self-id", "self",
            "--relay-peer", "peer1",
        ])
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_missing_self_id_returns_config_error(self, tmp_path):
        db = _make_db(tmp_path)
        # ensure env not set from test runner
        old = os.environ.pop(tickmod.SELF_ID_ENV, None)
        try:
            rc = tickmod.main(["--liaison-db", str(db)])
        finally:
            if old is not None:
                os.environ[tickmod.SELF_ID_ENV] = old
        assert rc == 3