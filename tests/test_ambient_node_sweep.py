# tests/test_ambient_node_sweep.py
import json
import os
import time

import pytest

from conscio.ambient import board, doctor, node, paths, surface
from conscio.liaison import directory


def _proc(tmp_path, *, load=0.5, mem_kb=4_096_000):
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "loadavg").write_text(f"{load:.2f} 0.40 0.30 1/100 123\n")
    (proc / "meminfo").write_text(f"MemTotal: 16000000 kB\nMemAvailable: {mem_kb} kB\n")
    return proc


def _enable(root):
    p = paths.flag_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("enabled\n")


def _samples(n, *, step, end, load=1.0):
    return [(end - step * i, load, 8000) for i in range(n)]


def _working(db, *, lease_s=10, now=None, files=()):
    fence = board.acquire_orchestration(db, holder="O", now=time.time())
    tid = board.create_task(db, title="t", body="b", creator="O", assignee="A",
                            files=files, orch_fence=fence)
    return tid, board.claim_task(db, task_id=tid, claimer="A", lease_s=lease_s,
                                 now=now if now is not None else time.time())


def test_I1_one_board_per_relay_root(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "r1"))
    a, b = node.Node(self_id="a"), node.Node(self_id="b")
    assert a.board == b.board == paths.board_path() == tmp_path / "r1" / "ambient" / "board.db"
    assert node.Node(self_id="a", root=tmp_path / "r2").board != a.board


def test_flag_off_reactor_never_opens_board(tmp_path, monkeypatch):
    def must_not_open(*a, **k):
        raise AssertionError("board opened with the flag off")
    monkeypatch.setattr(board, "open_board", must_not_open)
    n = node.Node(self_id="a", root=tmp_path / "relay", proc_root=_proc(tmp_path))
    n.on_tick()
    assert not n.is_sweeper
    assert not paths.board_path(tmp_path / "relay").exists()


def test_board_too_new_skips_sweep(tmp_path):
    import sqlite3
    root = tmp_path / "relay"
    _enable(root)
    raw = sqlite3.connect(paths.board_path(root))
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()
    n = node.Node(self_id="a", root=root, proc_root=_proc(tmp_path))
    n.on_tick()
    raw = sqlite3.connect(paths.board_path(root))
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
        assert raw.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    finally:
        raw.close()
    assert not n.is_sweeper


def test_I12_single_sweeper_three_reactors(tmp_path):
    # flock(2) conflicts between open file descriptions even inside one process,
    # so three Node objects behave like three reactors for the lock.
    root, proc = tmp_path / "relay", _proc(tmp_path)
    _enable(root)
    nodes = [node.Node(self_id=f"r{i}", root=root, proc_root=proc, clock=lambda: 1000.0)
             for i in range(3)]
    for n in nodes:
        n.on_tick()
    assert [n.is_sweeper for n in nodes] == [True, False, False]
    db = board.open_board(paths.board_path(root))
    try:
        assert len(board.recent_samples(db)) == 1
        sweeper = db.execute("SELECT actor FROM board_events WHERE kind='sweeper'").fetchall()
        assert [r[0] for r in sweeper] == ["r0"]
    finally:
        db.close()
    nodes[0].close()
    nodes[1].on_tick()
    assert nodes[1].is_sweeper
    for n in nodes:
        n.close()


def test_admission_uses_injected_samples_and_baseline_not_ready():
    now = 10_000.0
    kw = {"now": now, "mem_available_mb_now": 9000}
    assert node.admission(_samples(35, step=5, end=now), load1_now=0.1, **kw) == "baseline_not_ready"
    assert node.admission(_samples(36, step=12, end=now), load1_now=0.1, **kw) == "baseline_not_ready"
    ready = _samples(36, step=5, end=now, load=1.0)
    assert node.admission(ready, load1_now=1.0 + node.DELTA_TOLERADO, **kw) == ""
    assert node.admission(ready, load1_now=2.6, **kw) == "load"
    assert node.admission(ready, now=now, load1_now=0.5,
                          mem_available_mb_now=node.WAKE_FLOOR_MB - 1) == "mem"


def test_admission_ready_with_drifted_tick():
    # 5 s of sleep plus a 300 ms tick: 36 samples span 185.5 s, past the
    # draft's 180 s rule that could never close (spec §7.3).
    now = 10_000.0
    drifted = _samples(36, step=5.3, end=now)
    assert now - min(s[0] for s in drifted) > 180
    assert node.admission(drifted, now=now, load1_now=1.0, mem_available_mb_now=9000) == ""


def test_lease_expired_carries_session_and_doctor_lists_wake_residue(tmp_path):
    db = board.open_board(tmp_path / "board.db")
    t0 = time.time()
    tid, claim = _working(db, now=t0)
    db.execute("INSERT INTO sessions (session_id, instance_id, task_id, connector, pid,"
               " started_ts, state) VALUES ('s1', 'A', ?, 'claude-bg', 4242, ?, 'running')",
               (tid, t0))
    expired = board.expire_leases(db, now=t0 + 11)
    assert expired == [{"task_id": tid, "to": "backlog", "attempts": 1,
                        "fence": claim.fence + 1, "session": "s1", "pid": 4242}]
    row = board._task(db, tid)
    assert (row["state"], row["resumable"], row["fence"]) == ("backlog", "s1", claim.fence + 1)
    payload = json.loads(db.execute("SELECT payload FROM board_events WHERE kind="
                                    "'lease_expired'").fetchone()[0])
    assert (payload["session"], payload["pid"]) == ("s1", 4242)
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "4242" / "environ").write_bytes(b"PATH=/bin\0CONSCIO_WAKE_TASK=%d\0" % tid)
    (proc / "99").mkdir()
    (proc / "99" / "environ").write_bytes(b"PATH=/bin\0")
    assert doctor.wake_residue(db, proc_root=proc) == [
        {"pid": 4242, "task_id": tid, "state": "backlog"}]
    db.close()


def test_expired_executor_every_write_is_stale(tmp_path):
    db = board.open_board(tmp_path / "board.db")
    t0 = time.time()
    tid, claim = _working(db, now=t0)
    board.expire_leases(db, now=t0 + 11)
    for write in (
        lambda: board.renew_task(db, task_id=tid, fence=claim.fence, claimer="A", now=t0 + 12),
        lambda: board.submit_task(db, task_id=tid, fence=claim.fence, claimer="A"),
        lambda: board.release_task(db, task_id=tid, fence=claim.fence, claimer="A", reason="x"),
        lambda: board.block_task(db, task_id=tid, actor="A", reason="x", fence=claim.fence),
    ):
        with pytest.raises(board.StaleFence):
            write()
    db.close()


def test_reservation_released_on_lease_expiry(tmp_path):
    db = board.open_board(tmp_path / "board.db")
    t0 = time.time()
    _working(db, now=t0, files=[str(tmp_path / "f.py")])
    board.expire_leases(db, now=t0 + 11)
    assert db.execute("SELECT COUNT(*) FROM task_files WHERE reserved=1").fetchone()[0] == 0
    db.close()


def test_second_expiry_blocks(tmp_path):
    db = board.open_board(tmp_path / "board.db")
    t0 = time.time()
    tid, _ = _working(db, now=t0)
    board.expire_leases(db, now=t0 + 11)
    board.claim_task(db, task_id=tid, claimer="A", lease_s=10, now=t0 + 20)
    board.expire_leases(db, now=t0 + 31)
    row = board._task(db, tid)
    assert (row["state"], row["attempts"]) == ("blocked", 2)
    db.close()


def test_orchestration_expired_recorded_once(tmp_path):
    db = board.open_board(tmp_path / "board.db")
    t0 = time.time()
    board.acquire_orchestration(db, holder="O", ttl_s=10, now=t0)
    assert board.orchestration_expired(db, now=t0 + 5) is False
    assert board.orchestration_expired(db, now=t0 + 11) is True
    assert board.orchestration_expired(db, now=t0 + 12) is False
    db.close()


class _Stop(BaseException):
    pass


def test_node_exception_never_stops_relay_tick(tmp_path, monkeypatch):
    from conscio.liaison import reactor
    ticks = []
    monkeypatch.setattr(reactor, "dispatch", lambda *a, **k: ticks.append(1) or 0)

    def explode(self):
        raise RuntimeError("board on fire")
    monkeypatch.setattr(node.Node, "on_tick", explode)
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            raise _Stop()
    monkeypatch.setattr(reactor.time, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        reactor.main(["--storage", str(tmp_path / "space"), "--self-id", "me",
                      "--notify-cmd", "true", "--liaison-db", str(tmp_path / "liaison.db")])
    assert len(ticks) == 3


def test_broken_ambient_import_never_stops_delivery(tmp_path, monkeypatch, capsys):
    import sys

    from conscio.liaison import reactor
    ticks = []
    monkeypatch.setattr(reactor, "dispatch", lambda *a, **k: ticks.append(1) or 0)
    monkeypatch.setitem(sys.modules, "conscio.ambient.node", None)
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            raise _Stop()
    monkeypatch.setattr(reactor.time, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        reactor.main(["--storage", str(tmp_path / "space"), "--self-id", "me",
                      "--notify-cmd", "true", "--liaison-db", str(tmp_path / "liaison.db")])
    assert len(ticks) == 3
    assert "ambient node unavailable" in capsys.readouterr().err


def test_node_constructor_failure_never_stops_delivery(tmp_path, monkeypatch, capsys):
    from conscio.liaison import reactor
    ticks = []
    monkeypatch.setattr(reactor, "dispatch", lambda *a, **k: ticks.append(1) or 0)

    def broken(self, *a, **k):
        raise RuntimeError("ctor broken")
    monkeypatch.setattr(node.Node, "__init__", broken)
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            raise _Stop()
    monkeypatch.setattr(reactor.time, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        reactor.main(["--storage", str(tmp_path / "space"), "--self-id", "me",
                      "--notify-cmd", "true", "--liaison-db", str(tmp_path / "liaison.db")])
    assert len(ticks) == 3
    assert "ambient node unavailable" in capsys.readouterr().err


def test_unwritable_ambient_dir_never_stops_delivery(tmp_path, caplog):
    root = tmp_path / "relay"
    _enable(root)
    paths.board_path(root).mkdir()          # a directory where the db should be
    n = node.Node(self_id="a", root=root, proc_root=_proc(tmp_path))
    n.on_tick()                              # must not raise
    assert "ambient node skipped this tick" in caplog.text


def test_I11_board_state_only_in_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    _enable(None)
    clock = [time.time()]
    n = node.Node(self_id="a", proc_root=_proc(tmp_path), clock=lambda: clock[0])
    surface.run_op({"op": "orchestrate", "action": "acquire"}, actor="O", space=tmp_path / "o")
    surface.run_op({"op": "create", "title": "t", "assignee": "A"}, actor="O", space=tmp_path / "o")
    surface.run_op({"op": "claim", "task_id": 1, "lease_s": 1}, actor="A", space=tmp_path / "a")
    n.on_tick()
    clock[0] += 10                     # past the lease: the second sweep expires it
    n.on_tick()
    n.close()
    db = board.open_board(paths.board_path())
    try:
        assert board._task(db, 1)["state"] == "backlog"
    finally:
        db.close()
    allowed = {"board.db", "board.db-wal", "board.db-shm", "board.db.sweep.lock",
               "enabled", "agents.json"}
    assert set(os.listdir(paths.ambient_dir())) <= allowed


def test_enable_disable_and_report_cli(tmp_path, monkeypatch, capsys):
    from conscio.ambient import cli
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    monkeypatch.setenv("CONSCIO_SELF_ID", "O")
    space = str(tmp_path / "space")
    assert cli.main(["--storage", space, "enable"]) == 0
    assert paths.flag_path().is_file()
    surface.run_op({"op": "propose", "title": "t"}, actor="O", space=tmp_path / "space")
    capsys.readouterr()
    assert cli.main(["--storage", space, "report", "--since", "8h"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == {"proposed": 1}
    assert cli.main(["--storage", space, "disable"]) == 0
    assert not paths.flag_path().exists()


def test_doctor_prune_keeps_recent_and_open_work(tmp_path):
    db = board.open_board(tmp_path / "board.db")
    old = time.time() - 100 * 86400
    for state in ("done", "cancelled", "backlog"):
        db.execute("INSERT INTO tasks (title, body, state, creator, created_ts, updated_ts)"
                   " VALUES ('t', 'b', ?, 'O', ?, ?)", (state, old, old))
    db.execute("INSERT INTO board_events (ts, kind, payload) VALUES (?, 'x', '{}')", (old,))
    board.log_event(db, "recent", None, None, {}, time.time())
    got = board.prune(db, now=time.time())
    assert got == {"tasks": 2, "events": 1}
    assert [r[0] for r in db.execute("SELECT state FROM tasks")] == ["backlog"]
    db.close()
