"""v1.5 Daemon — the heartbeat that turns Conscio into a living process.

Per cycle: poll sensors (each guarded) -> assemble world_state -> engine.run
(awake-gated) -> fire the on_cycle hook (v1.6 seam, empty by default) -> poll the
workspace. A failing sensor never kills the loop; SIGTERM finishes the cycle and
writes a heartbeat; a pidfile keeps two daemons off one state dir.
"""
import json
import os
import signal

import pytest

from conscio.agency.loop import RunReport
from conscio.daemon import Daemon, DaemonAlreadyRunning
from conscio.engine import ConsciousnessEngine
from conscio.perception import MockSensor, PerceptionFrame, SensorAdapter


class _Boom(SensorAdapter):
    name = "boom"

    def perceive(self):
        raise RuntimeError("sensor down")


def _frame(src="mock"):
    return PerceptionFrame(source=src, observations=["obs"], signals={"x": 1.0})


def _engine(tmp_path):
    return ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")


def test_cycle_perceives_assembles_runs_and_fires_hook(tmp_path):
    eng = _engine(tmp_path)
    seen = {}

    def hook(frames, result):
        seen["frames"], seen["result"] = frames, result

    d = Daemon(eng, sensors=[MockSensor([_frame()])], on_cycle=hook)
    try:
        result = d.cycle()
        assert isinstance(result, RunReport)
        assert seen["result"] is result
        assert len(seen["frames"]) == 1 and seen["frames"][0].source == "mock"
    finally:
        eng.close()


def test_failing_sensor_is_isolated(tmp_path):
    eng = _engine(tmp_path)
    captured = {}
    d = Daemon(eng, sensors=[_Boom(), MockSensor([_frame("host")])],
               on_cycle=lambda frames, r: captured.update(frames=frames))
    try:
        result = d.cycle()                       # must not raise
        assert isinstance(result, RunReport)
        # the good sensor was still perceived despite the bad one
        assert [f.source for f in captured["frames"]] == ["host"]
    finally:
        eng.close()


def test_asleep_cycle_is_reflect_only(tmp_path):
    eng = _engine(tmp_path)                       # default: asleep
    d = Daemon(eng, sensors=[MockSensor([_frame()])])
    try:
        result = d.cycle()
        assert result.stopped == "asleep"
        assert eng.pending() == []
    finally:
        eng.close()


def test_assemble_concatenates_world_states(tmp_path):
    eng = _engine(tmp_path)
    try:
        d = Daemon(eng, sensors=[])
        ws = d.assemble([_frame("host"), _frame("agent")])
        assert "[host]" in ws and "[agent]" in ws
    finally:
        eng.close()


def test_on_cycle_default_is_noop(tmp_path):
    eng = _engine(tmp_path)
    d = Daemon(eng, sensors=[MockSensor([_frame()])])   # no hook supplied
    try:
        d.cycle()                                 # the empty seam is live, no raise
    finally:
        eng.close()


def test_run_once_then_shutdown_writes_heartbeat_and_releases_pid(tmp_path):
    eng = _engine(tmp_path)
    d = Daemon(eng, sensors=[MockSensor([_frame()])])
    d.run(once=True)                              # shuts down at end
    assert d.heartbeat_path.exists()
    hb = json.loads(d.heartbeat_path.read_text())
    assert hb["cycles"] == 1
    assert not d.pidfile.exists()


def test_signal_handler_sets_stop_flag(tmp_path):
    eng = _engine(tmp_path)
    d = Daemon(eng, sensors=[])
    try:
        assert d.should_stop is False
        d._handle_signal(signal.SIGTERM, None)
        assert d.should_stop is True
    finally:
        eng.close()


def test_pidfile_blocks_second_daemon(tmp_path):
    eng = _engine(tmp_path)
    d1 = Daemon(eng, sensors=[])
    d1._acquire_pidfile()
    try:
        d2 = Daemon(eng, sensors=[], pidfile=d1.pidfile)
        with pytest.raises(DaemonAlreadyRunning):
            d2._acquire_pidfile()
    finally:
        d1._release_pidfile()
        eng.close()


def test_stale_pidfile_is_reclaimed(tmp_path):
    eng = _engine(tmp_path)
    try:
        d = Daemon(eng, sensors=[])
        d.pidfile.parent.mkdir(parents=True, exist_ok=True)
        d.pidfile.write_text("999999")           # almost-certainly-dead pid
        d._acquire_pidfile()                      # reclaim, no raise
        assert d.pidfile.read_text().strip() == str(os.getpid())
        d._release_pidfile()
    finally:
        eng.close()


def test_workspace_polled_each_cycle(tmp_path):
    eng = _engine(tmp_path)
    calls = {"n": 0}

    class _WS:
        def poll(self):
            calls["n"] += 1

    d = Daemon(eng, sensors=[MockSensor([_frame(), _frame()])], workspace=_WS())
    try:
        d.cycle()
        d.cycle()
        assert calls["n"] == 2
    finally:
        eng.close()


def test_main_once_exit_zero(tmp_path):
    from conscio.daemon import main
    rc = main(["--storage", str(tmp_path / "s"), "--sensors", "host", "--once"])
    assert rc == 0
