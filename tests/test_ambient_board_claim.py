# tests/test_ambient_board_claim.py
import multiprocessing
import os
import time

import pytest

from conscio.ambient import board


def _board(tmp_path):
    return board.open_board(tmp_path / "board.db")


def _new(db, *, assignee="A", files=(), reviewer=None):
    fence = board._lease_row(db)
    if fence is None or fence["expires_ts"] <= time.time():
        orch = board.acquire_orchestration(db, holder="O", now=time.time())
    else:
        orch = int(fence["fence"])
    return board.create_task(db, title="t", body="b", creator="O",
                             assignee=assignee, reviewer=reviewer,
                             files=files, orch_fence=orch)


def test_claim_requires_assignee(tmp_path):
    db = _board(tmp_path)
    tid = _new(db, assignee="A")
    with pytest.raises(board.NotAssignee) as err:
        board.claim_task(db, task_id=tid, claimer="B", now=time.time())
    assert str(err.value) == f"NotAssignee (task {tid}, assignee A)"
    db.close()


def test_claim_outside_backlog_is_not_claimable(tmp_path):
    db = _board(tmp_path)
    tid = board.propose_task(db, title="t", body="b", creator="P")
    with pytest.raises(board.NotClaimable) as err:
        board.claim_task(db, task_id=tid, claimer="A", now=time.time())
    assert str(err.value) == f"NotClaimable (task {tid}, state proposed)"
    db.close()


def test_I3_stale_fence_raises(tmp_path):
    db = _board(tmp_path)
    tid = _new(db)
    claim = board.claim_task(db, task_id=tid, claimer="A", now=time.time())
    assert claim.fence == 1
    with pytest.raises(board.StaleFence) as err:
        board.renew_task(db, task_id=tid, fence=0, claimer="A", now=time.time())
    assert str(err.value) == f"StaleFence (task {tid}, fence 0, current 1)"
    db.close()


def test_claim_reserves_files_atomically_or_not_at_all(tmp_path):
    db = _board(tmp_path)
    a, b, c = (str(tmp_path / n) for n in ("a", "b", "c"))
    t1 = _new(db, files=[a, b])
    t2 = _new(db, files=[b, c])
    board.claim_task(db, task_id=t1, claimer="A", now=time.time())
    with pytest.raises(board.FilesReserved) as err:
        board.claim_task(db, task_id=t2, claimer="A", now=time.time())
    assert str(err.value) == f"FilesReserved (task {t2}, path {b}, held by task {t1})"
    row = board._task(db, t2)
    assert (row["state"], row["fence"]) == ("backlog", 0)
    reserved = db.execute("SELECT COUNT(*) FROM task_files WHERE task_id=? AND reserved=1",
                          (t2,)).fetchone()[0]
    assert reserved == 0
    db.close()


def test_reservation_symlinks_to_same_target_collide(tmp_path):
    db = _board(tmp_path)
    target = tmp_path / "real.py"
    target.write_text("x")
    (tmp_path / "l1").symlink_to(target)
    (tmp_path / "l2").symlink_to(target)
    os.link(target, tmp_path / "hard.py")
    t1 = _new(db, files=[str(tmp_path / "l1")])
    t2 = _new(db, files=[str(tmp_path / "l2")])
    t3 = _new(db, files=[str(tmp_path / "hard.py")])
    board.claim_task(db, task_id=t1, claimer="A", now=time.time())
    with pytest.raises(board.FilesReserved):
        board.claim_task(db, task_id=t2, claimer="A", now=time.time())
    # known limit (§6.5): a hardlink has its own path and does NOT collide
    board.claim_task(db, task_id=t3, claimer="A", now=time.time())
    db.close()


def test_declared_path_spellings_share_one_reservation(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    db = _board(tmp_path)
    (tmp_path / "d").mkdir()
    t1 = _new(db, files=["~/d/x.py"])
    t2 = _new(db, files=[str(tmp_path / "d" / ".." / "d" / "x.py")])
    board.claim_task(db, task_id=t1, claimer="A", now=time.time())
    with pytest.raises(board.FilesReserved):
        board.claim_task(db, task_id=t2, claimer="A", now=time.time())
    db.close()


def test_execute_only_without_orchestration_lease(tmp_path):
    db = _board(tmp_path)
    tid = _new(db)
    lease = board._lease_row(db)
    board.release_orchestration(db, holder="O", orch_fence=int(lease["fence"]))
    claim = board.claim_task(db, task_id=tid, claimer="A", now=time.time())
    board.renew_task(db, task_id=tid, fence=claim.fence, claimer="A", now=time.time())
    with pytest.raises(board.NoOrchestrationLease):
        board.create_task(db, title="t", body="b", creator="O", assignee="A",
                          orch_fence=int(lease["fence"]))
    db.close()


def test_renew_extends_lease_requires_matching_fence(tmp_path):
    db = _board(tmp_path)
    tid = _new(db)
    now = time.time()
    claim = board.claim_task(db, task_id=tid, claimer="A", lease_s=60, now=now)
    board.renew_task(db, task_id=tid, fence=claim.fence, claimer="A",
                     lease_s=600, now=now + 30)
    assert board._task(db, tid)["lease_expires_ts"] == pytest.approx(now + 630)
    with pytest.raises(board.StaleFence):
        board.renew_task(db, task_id=tid, fence=claim.fence + 5, claimer="A",
                         lease_s=9999, now=now + 40)
    assert board._task(db, tid)["lease_expires_ts"] == pytest.approx(now + 630)
    db.close()


def _claim_worker(path, task_id, barrier, queue):
    db = board.open_board(path)
    try:
        barrier.wait()
        try:
            board.claim_task(db, task_id=task_id, claimer="A", now=time.time())
            queue.put("won")
        except board.BoardError as exc:
            queue.put(type(exc).__name__)
    finally:
        db.close()


def _open_worker(path, barrier, queue):
    barrier.wait()
    try:
        board.open_board(path).close()
        queue.put("ok")
    except Exception as exc:                      # report, never hang the parent
        queue.put(type(exc).__name__)


def test_open_board_concurrent_creation_is_idempotent(tmp_path):
    # H34 phase record: several reactors may create the board at the same instant.
    path = tmp_path / "fresh" / "board.db"
    ctx = multiprocessing.get_context("fork")
    barrier, queue = ctx.Barrier(4), ctx.Queue()
    procs = [ctx.Process(target=_open_worker, args=(path, barrier, queue)) for _ in range(4)]
    for p in procs:
        p.start()
    results = sorted(queue.get(timeout=30) for _ in procs)
    for p in procs:
        p.join(timeout=30)
    assert results == ["ok"] * 4
    db = board.open_board(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        db.close()


def test_I3_claim_atomic_8_processes_one_winner(tmp_path):
    path = tmp_path / "board.db"
    db = board.open_board(path)
    tid = _new(db)
    db.close()
    ctx = multiprocessing.get_context("fork")
    barrier, queue = ctx.Barrier(8), ctx.Queue()
    procs = [ctx.Process(target=_claim_worker, args=(path, tid, barrier, queue))
             for _ in range(8)]
    for p in procs:
        p.start()
    results = sorted(queue.get(timeout=30) for _ in procs)
    for p in procs:
        p.join(timeout=30)
    assert results.count("won") == 1
    assert results.count("ClaimLost") == 7


def test_claim_and_renew_accept_now_none(tmp_path):
    """H35-1: every write accepts now=None, the lease ones included."""
    db = _board(tmp_path)
    tid = _new(db)
    before = time.time()
    c = board.claim_task(db, task_id=tid, claimer="A", lease_s=60)
    board.renew_task(db, task_id=tid, fence=c.fence, claimer="A", lease_s=120)
    after = time.time()
    exp = board._task(db, tid)["lease_expires_ts"]
    assert before + 60 <= c.lease_expires_ts <= after + 60
    assert before + 120 <= exp <= after + 120
