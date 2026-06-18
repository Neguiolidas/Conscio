# tests/test_daemon_heartbeat.py
"""v1.6 (#5/#9): the daemon heartbeat artifact carries Conscio's output.

Before v1.6 the heartbeat was {ts, cycles, awake, pid} — liveness only. A host
running the daemon out-of-process had nothing to read. Now each cycle writes the
last-run summary + the engine.advisory() snapshot, so a host can simply
`tail -f daemon_heartbeat.json` for canonical, always-current signal. Writes stay
best-effort and a failing advisory never breaks the heartbeat.
"""
import json

from conscio.daemon import Daemon
from conscio.engine import ConsciousnessEngine
from conscio.perception import MockSensor, PerceptionFrame


def _frame():
    return PerceptionFrame(source="mock", observations=["obs"], signals={"x": 1.0})


def _daemon(tmp_path, **kw):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    return eng, Daemon(eng, sensors=[MockSensor([_frame()])], **kw)


def test_heartbeat_keeps_liveness_keys(tmp_path):
    eng, d = _daemon(tmp_path)
    try:
        d.cycle()
        hb = json.loads(d.heartbeat_path.read_text())
        assert {"ts", "cycles", "awake", "pid"} <= set(hb)
    finally:
        eng.close()


def test_heartbeat_includes_advisory_snapshot(tmp_path):
    eng, d = _daemon(tmp_path)
    try:
        d.cycle()
        hb = json.loads(d.heartbeat_path.read_text())
        assert "advisory" in hb
        assert {"awake", "goals", "status", "recommendations"} <= set(hb["advisory"])
    finally:
        eng.close()


def test_heartbeat_includes_last_run_summary(tmp_path):
    eng, d = _daemon(tmp_path)
    try:
        d.cycle()
        hb = json.loads(d.heartbeat_path.read_text())
        assert "last_run" in hb
        assert {"cycles", "failures", "stopped"} <= set(hb["last_run"])
    finally:
        eng.close()


def test_advisory_failure_is_non_fatal(tmp_path):
    eng, d = _daemon(tmp_path)

    def boom():
        raise RuntimeError("advisory down")

    eng.advisory = boom                          # type: ignore[method-assign]
    try:
        d.cycle()                                # must not raise
        hb = json.loads(d.heartbeat_path.read_text())
        assert {"ts", "cycles", "awake", "pid"} <= set(hb)   # still written
        assert "advisory" not in hb                          # gracefully omitted
    finally:
        eng.close()


def test_shutdown_heartbeat_has_advisory_without_a_cycle(tmp_path):
    # _write_heartbeat runs on shutdown too; advisory comes from engine state,
    # so it is present even when no cycle has produced a RunReport yet.
    eng, d = _daemon(tmp_path)
    try:
        d._write_heartbeat()
        hb = json.loads(d.heartbeat_path.read_text())
        assert "advisory" in hb
        assert "last_run" not in hb              # no cycle ran -> no run summary
    finally:
        eng.close()
