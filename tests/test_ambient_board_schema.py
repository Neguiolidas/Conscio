# tests/test_ambient_board_schema.py
import sqlite3

import pytest

from conscio.ambient import board


def _tables(db):
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_open_board_creates_schema_v1(tmp_path):
    db = board.open_board(tmp_path / "ambient" / "board.db")
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {"tasks", "task_files", "board_lease", "sessions",
                "admission_samples", "board_events"} <= _tables(db)
    finally:
        db.close()


def test_open_board_is_idempotent(tmp_path):
    path = tmp_path / "board.db"
    board.open_board(path).close()
    db = board.open_board(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        db.close()


def test_open_board_refuses_newer_schema_and_leaves_file_untouched(tmp_path):
    path = tmp_path / "board.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE future (x)")
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()
    with pytest.raises(board.BoardTooNew) as err:
        board.open_board(path)
    assert str(err.value) == "BoardTooNew (file v2, code v1)"
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "tasks" not in _tables(raw)
    finally:
        raw.close()


def test_lock_beyond_busy_timeout_is_boardbusy(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "BUSY_TIMEOUT_MS", 50)
    path = tmp_path / "board.db"
    holder = board.open_board(path)
    other = board.open_board(path)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(board.BoardBusy) as err, board._tx(other):
            pass
        assert str(err.value) == "BoardBusy (retry)"
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        other.close()


def test_error_texts_are_the_spec_matrix():
    cases = {
        board.ClaimLost(7): "ClaimLost (task 7)",
        board.NotAssignee(7, "A"): "NotAssignee (task 7, assignee A)",
        board.NotClaimable(7, "done"): "NotClaimable (task 7, state done)",
        board.FilesReserved(7, "/x", 3): "FilesReserved (task 7, path /x, held by task 3)",
        board.RelativePath("src/x.py"): "RelativePath (src/x.py)",
        board.StaleFence(7, 1, 2): "StaleFence (task 7, fence 1, current 2)",
        board.StaleFence(None, 1, 2): "StaleFence (orchestration, fence 1, current 2)",
        board.NoOrchestrationLease():
            "NoOrchestrationLease (backlog frozen; claim existing tasks or take the lease)",
        board.OrchestrationHeld("A", 0.0):
            "OrchestrationHeld (holder A, expires 1970-01-01T00:00:00+00:00)",
        board.SelfReview(7): "SelfReview (task 7)",
        board.BoardBusy(): "BoardBusy (retry)",
        board.BoardTooNew(2, 1): "BoardTooNew (file v2, code v1)",
        board.NoSuchTask(7): "NoSuchTask (task 7)",
        board.NotReviewer(7, "R"): "NotReviewer (task 7, reviewer R)",
    }
    for exc, text in cases.items():
        assert isinstance(exc, board.BoardError)
        assert str(exc) == text
