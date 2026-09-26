# tests/test_ambient_board_dispatch.py
import time

import pytest

from conscio.ambient import board


@pytest.fixture
def db(tmp_path):
    conn = board.open_board(tmp_path / "board.db")
    yield conn
    conn.close()


def _count(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_I9_non_holder_cannot_create_backlog(db, tmp_path):
    fence = board.acquire_orchestration(db, holder="A", now=time.time())
    with pytest.raises(board.StaleFence) as err:
        board.create_task(db, title="t", body="b", creator="B", assignee="C",
                          orch_fence=fence)
    assert str(err.value) == f"StaleFence (orchestration, fence {fence}, current {fence})"
    tid = board.propose_task(db, title="t", body="b", creator="B")
    assert board._task(db, tid)["state"] == "proposed"
    board.assign_task(db, task_id=tid, assignee="C", orch_fence=fence)
    row = board._task(db, tid)
    assert (row["state"], row["assignee"]) == ("backlog", "C")


def test_dispatch_without_live_lease_is_refused(db):
    with pytest.raises(board.NoOrchestrationLease):
        board.create_task(db, title="t", body="b", creator="A", assignee="C",
                          orch_fence=1)
    board.acquire_orchestration(db, holder="A", ttl_s=1, now=time.time() - 10)
    with pytest.raises(board.NoOrchestrationLease):
        board.create_task(db, title="t", body="b", creator="A", assignee="C",
                          orch_fence=1)
    assert _count(db, "tasks") == 0


def test_I5_orchestration_takeover_fence_plus_one_old_holder_stale(db):
    with pytest.raises(board.OrchestrationHeld) as held:
        board.acquire_orchestration(db, holder="A", ttl_s=60, now=0.0)
        board.acquire_orchestration(db, holder="B", now=30.0)
    assert str(held.value) == \
        "OrchestrationHeld (holder A, expires 1970-01-01T00:01:00+00:00)"
    old = board.acquire_orchestration(db, holder="A", ttl_s=60,
                                      now=time.time() - 120)
    new = board.acquire_orchestration(db, holder="B", now=time.time())
    assert new == old + 1
    with pytest.raises(board.StaleFence) as err:
        board.create_task(db, title="t", body="b", creator="A", assignee="C",
                          orch_fence=old)
    assert str(err.value) == f"StaleFence (orchestration, fence {old}, current {new})"


def test_dispatch_renews_orchestration_lease(db):
    fence = board.acquire_orchestration(db, holder="A", ttl_s=3060,
                                        now=time.time() - 3000)
    board.create_task(db, title="t", body="b", creator="A", assignee="C",
                      orch_fence=fence)
    assert board._lease_row(db)["expires_ts"] >= time.time() + 3500


def test_release_orchestration_freezes_dispatch(db):
    fence = board.acquire_orchestration(db, holder="A", now=time.time())
    board.release_orchestration(db, holder="A", orch_fence=fence)
    with pytest.raises(board.NoOrchestrationLease):
        board.create_task(db, title="t", body="b", creator="A", assignee="C",
                          orch_fence=fence)
    assert board.acquire_orchestration(db, holder="B", now=time.time()) == fence + 1


def test_board_propose_dedupe_by_origin(db):
    a = board.propose_task(db, title="t", body="b", creator="P", origin="relay:X:9")
    b = board.propose_task(db, title="t", body="b", creator="P", origin="relay:X:9")
    assert a == b
    assert _count(db, "tasks") == 1
    kinds = [r[0] for r in db.execute("SELECT kind FROM board_events")]
    assert kinds == ["proposed"]


def test_relative_path_refused(db, tmp_path):
    with pytest.raises(board.RelativePath) as err:
        board.propose_task(db, title="t", body="b", creator="P",
                           files=["src/x.py"])
    assert str(err.value) == "RelativePath (src/x.py)"
    assert _count(db, "tasks") == 0
    tid = board.propose_task(db, title="t", body="b", creator="P",
                             files=[str(tmp_path / "a.py"), "~/b.py"])
    paths = {r[0] for r in db.execute("SELECT path FROM task_files WHERE task_id=?", (tid,))}
    assert str((tmp_path / "a.py").resolve()) in paths
    assert all(p.startswith("/") for p in paths)


def test_every_write_records_event_in_same_transaction(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("event write failed")
    monkeypatch.setattr(board, "_event", boom)
    with pytest.raises(RuntimeError):
        board.propose_task(db, title="t", body="b", creator="P")
    assert _count(db, "tasks") == 0


def test_lease_writes_accept_now_none(db):
    before = time.time()
    fence = board.acquire_orchestration(db, holder="A", ttl_s=60)
    board.renew_orchestration(db, holder="A", orch_fence=fence, ttl_s=120)
    after = time.time()
    row = db.execute("SELECT fence, acquired_ts, expires_ts FROM board_lease WHERE id = 1").fetchone()
    assert row[0] == fence
    assert before <= row[1] <= after
    assert before + 120 <= row[2] <= after + 120


def test_board_propose_dedupe_key_is_origin_not_title(db):
    a = board.propose_task(db, title="t1", body="b", creator="P", origin="relay:X:9")
    b = board.propose_task(db, title="t2", body="b", creator="P", origin="relay:X:9")
    assert a == b
    c = board.propose_task(db, title="t1", body="b", creator="P", origin="relay:X:10")
    assert c != a
    assert _count(db, "tasks") == 2
    kinds = [r[0] for r in db.execute("SELECT kind FROM board_events ORDER BY id")]
    assert kinds == ["proposed", "proposed"]
