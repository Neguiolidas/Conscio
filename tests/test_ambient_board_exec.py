# tests/test_ambient_board_exec.py
import time

import pytest

from conscio.ambient import board


@pytest.fixture
def db(tmp_path):
    conn = board.open_board(tmp_path / "board.db")
    yield conn
    conn.close()


def _orch(db):
    row = board._lease_row(db)
    if row is not None and row["expires_ts"] > time.time():
        return int(row["fence"])
    return board.acquire_orchestration(db, holder="O", now=time.time())


def _working(db, *, assignee="A", reviewer=None, files=()):
    tid = board.create_task(db, title="t", body="b", creator="O", assignee=assignee,
                            reviewer=reviewer, files=files, orch_fence=_orch(db))
    return tid, board.claim_task(db, task_id=tid, claimer=assignee, now=time.time())


def test_I6_self_review_refused(db):
    tid, c = _working(db, reviewer="R")
    board.submit_task(db, task_id=tid, fence=c.fence, claimer="A")
    with pytest.raises(board.SelfReview) as err:
        board.review_task(db, task_id=tid, fence=c.fence, reviewer="A", verdict="approve")
    assert str(err.value) == f"SelfReview (task {tid})"


def test_review_by_someone_else_than_the_reviewer_refused(db):
    tid, c = _working(db, reviewer="R")
    board.submit_task(db, task_id=tid, fence=c.fence, claimer="A")
    with pytest.raises(board.NotReviewer) as err:
        board.review_task(db, task_id=tid, fence=c.fence, reviewer="X", verdict="approve")
    assert str(err.value) == f"NotReviewer (task {tid}, reviewer R)"
    board.review_task(db, task_id=tid, fence=c.fence, reviewer="R", verdict="approve")
    assert board._task(db, tid)["state"] == "done"


def test_submit_without_reviewer_goes_straight_to_done(db):
    tid, c = _working(db)
    board.submit_task(db, task_id=tid, fence=c.fence, claimer="A")
    assert board._task(db, tid)["state"] == "done"


def test_reject_returns_to_backlog_same_assignee_attempts_intact(db):
    tid, c = _working(db, reviewer="R")
    board.submit_task(db, task_id=tid, fence=c.fence, claimer="A")
    board.review_task(db, task_id=tid, fence=c.fence, reviewer="R", verdict="reject")
    row = board._task(db, tid)
    assert (row["state"], row["assignee"], row["attempts"]) == ("backlog", "A", 0)


def test_release_counts_attempts_then_blocks(db):
    tid, c = _working(db)
    board.release_task(db, task_id=tid, fence=c.fence, claimer="A", reason="stuck")
    assert (board._task(db, tid)["state"], board._task(db, tid)["attempts"]) == ("backlog", 1)
    c2 = board.claim_task(db, task_id=tid, claimer="A", now=time.time())
    board.release_task(db, task_id=tid, fence=c2.fence, claimer="A", reason="stuck")
    assert (board._task(db, tid)["state"], board._task(db, tid)["attempts"]) == ("blocked", 2)
    kinds = [r[0] for r in db.execute("SELECT kind FROM board_events WHERE task_id=? ORDER BY id", (tid,))]
    assert kinds[-2:] == ["released", "blocked"]


EXITS = ["submit_done", "submit_review", "release", "block_exec", "block_orch", "cancel"]


@pytest.mark.parametrize("exit_", EXITS)
def test_reservation_released_on_every_exit_from_working(db, tmp_path, exit_):
    f = str(tmp_path / "shared.py")
    reviewer = "R" if exit_ == "submit_review" else None
    tid, c = _working(db, reviewer=reviewer, files=[f])
    if exit_.startswith("submit"):
        board.submit_task(db, task_id=tid, fence=c.fence, claimer="A")
    elif exit_ == "release":
        board.release_task(db, task_id=tid, fence=c.fence, claimer="A", reason="x")
    elif exit_ == "block_exec":
        board.block_task(db, task_id=tid, actor="A", reason="x", fence=c.fence)
    elif exit_ == "block_orch":
        board.block_task(db, task_id=tid, actor="O", reason="x", orch_fence=_orch(db))
    else:
        board.cancel_task(db, task_id=tid, actor="O", reason="x", orch_fence=_orch(db))
    assert db.execute("SELECT COUNT(*) FROM task_files WHERE reserved=1").fetchone()[0] == 0
    other, _ = _working(db, assignee="B", files=[f])
    assert board._task(db, other)["state"] == "working"


def test_cancel_requires_the_holder(db):
    tid, _ = _working(db)
    fence = _orch(db)
    with pytest.raises(board.StaleFence):
        board.cancel_task(db, task_id=tid, actor="X", reason="x", orch_fence=fence)


def test_show_list_and_status(db, tmp_path):
    tid, _ = _working(db, files=[str(tmp_path / "a.py")])
    shown = board.show_task(db, tid)
    assert shown["state"] == "working"
    assert shown["files"] == [{"path": str((tmp_path / "a.py").resolve()), "reserved": True}]
    assert [t["id"] for t in board.list_tasks(db, assignee="A")] == [tid]
    assert board.list_tasks(db, state="done") == []
    status = board.board_status(db, now=time.time())
    assert status["holder"] == "O" and status["counts"] == {"working": 1}
    with pytest.raises(board.NoSuchTask):
        board.show_task(db, 999)
