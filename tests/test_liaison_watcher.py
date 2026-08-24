# tests/test_liaison_watcher.py
"""Tests for conscio.liaison.watcher — native A2A relay watchdog.

Covers per-peer cursor, payload filtering, exit-code contract, and the
silent-when-idle cron contract. Origin: external relay_watch_hermes.py
replaced with a native module so peers/self_id have a single source of
truth and the cursor survives restarts in liaison.db itself.
"""
import sqlite3

import pytest

from conscio.liaison import mailbox
from conscio.liaison.watcher import (
    ExitCode,
    _load_state,
    poll_digest,
    tick_once,
)

# ── Fixtures ──────────────────────────────────────────────────────────

SELF = "self-1111"
PEER_A = "peer-aaaa"
PEER_B = "peer-bbbb"
STRANGER = "stranger-9999"


def _build_mailbox(db):
    """Populate a liaison.db with the standard cross-peer mix."""
    mailbox.send(db, from_instance=PEER_A, to_instance=SELF,
                 type="note", payload={"hi": 1})                 # id 1
    mailbox.send(db, from_instance=SELF, to_instance=PEER_A,
                 type="note", payload={"selfout": 1})            # id 2 (self)
    mailbox.send(db, from_instance=STRANGER, to_instance=SELF,
                 type="note", payload={"x": 1})                  # id 3
    mailbox.send(db, from_instance=PEER_A, to_instance=SELF,
                 type="review_request", payload={})              # id 4 reserved
    mailbox.send(db, from_instance=PEER_A, to_instance=SELF,
                 type="note", payload={"second": 2})             # id 5
    mailbox.send(db, from_instance=PEER_B, to_instance=SELF,
                 type="note", payload={"fromb": 1})              # id 6
    return db


@pytest.fixture
def mailbox_db(tmp_path):
    return _build_mailbox(tmp_path / "liaison.db")


def _max_id(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]
    finally:
        con.close()


# ── poll_digest: pure selection ────────────────────────────────────────

class TestPollDigest:
    def test_selects_peer_messages_after_cursor(self, mailbox_db):
        msgs = poll_digest(mailbox_db, since_id=0, self_id=SELF,
                           peers=[PEER_A, PEER_B])
        assert {m["id"] for m in msgs} == {1, 5, 6}
        for m in msgs:
            assert m["from_instance"] in {PEER_A, PEER_B}

    def test_cursor_filters_older(self, mailbox_db):
        msgs = poll_digest(mailbox_db, since_id=1, self_id=SELF,
                           peers=[PEER_A, PEER_B])
        assert {m["id"] for m in msgs} == {5, 6}

    def test_single_peer_filters_others(self, mailbox_db):
        msgs = poll_digest(mailbox_db, since_id=0, self_id=SELF,
                           peers=[PEER_A])
        assert {m["id"] for m in msgs} == {1, 5}
        assert all(m["from_instance"] == PEER_A for m in msgs)

    def test_reserved_types_never_surfaced(self, mailbox_db):
        msgs = poll_digest(mailbox_db, since_id=0, self_id=SELF,
                           peers=[PEER_A])
        assert all(m["type"] != "review_request" for m in msgs)
        assert all(m["type"] != "review_verdict" for m in msgs)

    def test_ignores_self_and_stranger(self, mailbox_db):
        msgs = poll_digest(mailbox_db, since_id=0, self_id=SELF,
                           peers=[PEER_A, PEER_B])
        assert all(m["from_instance"] in {PEER_A, PEER_B} for m in msgs)

    def test_missing_db_degrades_empty(self, tmp_path):
        assert poll_digest(tmp_path / "nope.db", 0, SELF, [PEER_A]) == []

    def test_empty_peers_returns_empty(self, mailbox_db):
        assert poll_digest(mailbox_db, 0, SELF, []) == []

    def test_empty_self_id_returns_empty(self, mailbox_db):
        assert poll_digest(mailbox_db, 0, "", [PEER_A]) == []

    def test_max_id_cursor(self, mailbox_db):
        assert _max_id(mailbox_db) == 6

    def test_payload_decoded(self, mailbox_db):
        msgs = poll_digest(mailbox_db, since_id=0, self_id=SELF,
                           peers=[PEER_A])
        assert msgs[0]["payload"] == {"hi": 1}


# ── watcher_state: per-peer persistence ──────────────────────────────

class TestState:
    def test_roundtrip_state(self, tmp_path):
        from conscio.liaison.watcher import _save_state
        st = tmp_path / "liaison.db"
        _save_state(st, {
            PEER_A: {"last_seen_id": 5, "status": "idle"},
            PEER_B: {"last_seen_id": 9, "status": "pending_consumption"},
        })
        loaded = _load_state(st)
        assert loaded[PEER_A] == {"last_seen_id": 5, "status": "idle"}
        assert loaded[PEER_B] == {"last_seen_id": 9,
                                  "status": "pending_consumption"}

    def test_load_missing_returns_empty(self, tmp_path):
        assert _load_state(tmp_path / "missing.db") == {}

    def test_upsert_overwrites_existing(self, tmp_path):
        from conscio.liaison.watcher import _save_state
        st = tmp_path / "liaison.db"
        _save_state(st, {PEER_A: {"last_seen_id": 5, "status": "idle"}})
        _save_state(st, {PEER_A: {"last_seen_id": 9, "status": "pending"}})
        assert _load_state(st)[PEER_A] == {"last_seen_id": 9, "status": "pending"}


# ── tick_once: end-to-end contract ────────────────────────────────────

class TestTickOnce:
    def test_consumed_then_idle(self, mailbox_db):
        """First tick consumes all, second tick is silent-idle."""
        outbox = mailbox_db.parent / "relay_inbox.json"
        msgs, code = tick_once(mailbox_db, self_id=SELF,
                               peers=[PEER_A, PEER_B], outbox=outbox)
        assert code == ExitCode.OK
        assert {m["id"] for m in msgs} == {1, 5, 6}
        assert outbox.exists()

        msgs2, code2 = tick_once(mailbox_db, self_id=SELF,
                                 peers=[PEER_A, PEER_B], outbox=outbox)
        assert msgs2 == []
        assert code2 == ExitCode.OK

    def test_new_messages_advance_per_peer_cursor(self, mailbox_db):
        outbox = mailbox_db.parent / "relay_inbox.json"
        _, code = tick_once(mailbox_db, self_id=SELF,
                               peers=[PEER_A, PEER_B], outbox=outbox)
        assert code == ExitCode.OK
        state = _load_state(mailbox_db)
        assert state[PEER_A]["last_seen_id"] == 5
        assert state[PEER_B]["last_seen_id"] == 6
        assert state[PEER_A]["status"] == "pending_consumption"

    def test_outbox_written_with_peer_grouping(self, mailbox_db):
        outbox = mailbox_db.parent / "relay_inbox.json"
        tick_once(mailbox_db, self_id=SELF, peers=[PEER_A, PEER_B],
                  outbox=outbox)
        import json
        data = json.loads(outbox.read_text())
        assert data["self_id"] == SELF
        assert set(data["messages_by_peer"].keys()) == {PEER_A, PEER_B}
        assert data["status"] == "pending_consumption"

    def test_outbox_failure_marks_pending_capture(self, tmp_path):
        db = tmp_path / "liaison.db"
        _build_mailbox(db)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        bad_outbox = blocker / "relay_inbox.json"
        _, code = tick_once(db, self_id=SELF,
                               peers=[PEER_A], outbox=bad_outbox)
        assert code == ExitCode.PENDING_CAPTURE
        # cursor NOT advanced — next tick must re-surface.
        state = _load_state(db)
        assert state[PEER_A]["status"] == "pending_capture"
        assert state[PEER_A]["last_seen_id"] == 0

    def test_missing_db_is_config_error(self, tmp_path):
        msgs, code = tick_once(tmp_path / "nope.db", self_id=SELF,
                               peers=[PEER_A], outbox=None)
        assert msgs == []
        assert code == ExitCode.CONFIG_ERROR

    def test_no_peers_is_config_error(self, mailbox_db):
        msgs, code = tick_once(mailbox_db, self_id=SELF,
                               peers=[], outbox=None)
        assert msgs == []
        assert code == ExitCode.CONFIG_ERROR

    def test_no_self_id_is_config_error(self, mailbox_db):
        msgs, code = tick_once(mailbox_db, self_id="",
                               peers=[PEER_A], outbox=None)
        assert msgs == []
        assert code == ExitCode.CONFIG_ERROR

    def test_no_outbox_surfaces_and_advances_cursor(self, mailbox_db):
        """Without an outbox, stdout IS the delivery: the watcher prints the
        new messages AND advances the per-peer cursor (legacy cron contract).
        Next tick is then silent — no re-spam of the same set."""
        msgs, code = tick_once(mailbox_db, self_id=SELF,
                               peers=[PEER_A, PEER_B], outbox=None)
        assert code == ExitCode.OK
        assert {m["id"] for m in msgs} == {1, 5, 6}
        # cursor advanced because stdout delivered them
        st = _load_state(mailbox_db)
        assert st[PEER_A]["last_seen_id"] == 5
        assert st[PEER_B]["last_seen_id"] == 6
        # and the next tick is silent-idle (no re-delivery / spam)
        msgs2, code2 = tick_once(mailbox_db, self_id=SELF,
                                 peers=[PEER_A, PEER_B], outbox=None)
        assert msgs2 == []
        assert code2 == ExitCode.OK

    def test_pending_capture_does_not_advance_other_peer(self, mailbox_db):
        """Peer-B advances even when Peer-A's outbox fails? No: outbox is
        global — a single emit failure stalls all cursors. This asserts the
        conservative contract (no partial advance)."""
        # Point outbox at dir we cannot create files in: use a file as parent.
        from pathlib import Path
        blocker = Path(mailbox_db).parent / "blocker"
        blocker.write_text("x")
        bad_outbox = blocker / "inbox.json"
        _, code = tick_once(mailbox_db, self_id=SELF,
                               peers=[PEER_A, PEER_B], outbox=bad_outbox)
        assert code == ExitCode.PENDING_CAPTURE
        state = _load_state(mailbox_db)
        # No cursor advanced because emit (shared) failed.
        assert all(st["last_seen_id"] == 0 for st in state.values())


# ── Exit-code contract ────────────────────────────────────────────────

class TestExitCode:
    def test_enum_values_are_honest(self):
        assert int(ExitCode.OK) == 0
        assert int(ExitCode.PENDING_CAPTURE) == 2
        assert int(ExitCode.CONFIG_ERROR) == 3


# ── Ato 3a: legacy-compat flags (--since, --interval) ──────────────────────

class TestLegacyCompat:
    def test_since_rewinds_then_tick_resurfaces(self, mailbox_db, capsys):
        """Legacy --since rewinds the per-peer cursor; the single --once tick
        that main() performs then re-surfaces everything after that id
        (replay/recover hook) — printed and cursor re-advanced."""
        from conscio.liaison.watcher import main as wmain
        # consume everything first (cursor for PEER_A ends at 5)
        outbox = mailbox_db.parent / "inbox.json"
        tick_once(mailbox_db, self_id=SELF, peers=[PEER_A, PEER_B],
                  outbox=outbox)
        assert _load_state(mailbox_db)[PEER_A]["last_seen_id"] == 5

        # --since 1 rewinds, then main's single tick re-surfaces ids 5,6
        rc = wmain([
            "--liaison-db", str(mailbox_db),
            "--self-id", SELF,
            "--relay-peer", PEER_A, "--relay-peer", PEER_B,
            "--since", "1",
        ])
        assert rc == 0
        # the replay tick printed both peer ids 5 and 6
        out = capsys.readouterr().out
        assert '"id": 5' in out and '"id": 6' in out
        # cursor re-advanced to the new max (5 and 6)
        st = _load_state(mailbox_db)
        assert st[PEER_A]["last_seen_id"] == 5
        assert st[PEER_B]["last_seen_id"] == 6

    def test_since_requires_self_id(self, mailbox_db):
        from conscio.liaison.watcher import main as wmain
        rc = wmain(["--liaison-db", str(mailbox_db),
                    "--self-id", "", "--relay-peer", PEER_A])
        assert rc == int(ExitCode.CONFIG_ERROR)