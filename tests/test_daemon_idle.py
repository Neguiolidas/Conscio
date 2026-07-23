"""Tests for daemon idle behaviour (v3.3 — no signal → no engine.run)."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from conscio.daemon import Daemon
from conscio.perception.sensor import MockSensor, PerceptionFrame
from conscio.agency.loop import RunReport


def _make_daemon(d, sensors, engine=None):
    """Create a Daemon with minimal deps."""
    if engine is None:
        engine = MagicMock()
        engine.awake = True
        engine.run.return_value = RunReport()
    engine.advisory = None  # prevent JSON serialization error in heartbeat
    return Daemon(
        engine=engine,
        sensors=sensors,
        pidfile=Path(d) / "p.pid",
        heartbeat_path=Path(d) / "hb.json",
    )


def test_idle_daemon_does_not_call_engine_run():
    with tempfile.TemporaryDirectory() as d:
        sensor = MockSensor([PerceptionFrame(source="empty", observations=[])])
        engine = MagicMock()
        engine.awake = True
        daemon = _make_daemon(d, [sensor], engine)
        daemon.cycle()
        engine.run.assert_not_called()


def test_signal_daemon_calls_engine_run():
    with tempfile.TemporaryDirectory() as d:
        sensor = MockSensor([
            PerceptionFrame(source="fs", observations=["modified: x.py"])
        ])
        engine = MagicMock()
        engine.awake = True
        engine.run.return_value = RunReport()
        daemon = _make_daemon(d, [sensor], engine)
        daemon.cycle()
        engine.run.assert_called_once()


def test_idle_increments_counter():
    with tempfile.TemporaryDirectory() as d:
        sensor = MockSensor([
            PerceptionFrame(source="empty", observations=[]),
            PerceptionFrame(source="empty", observations=[]),
        ])
        engine = MagicMock()
        engine.awake = True
        daemon = _make_daemon(d, [sensor], engine)
        daemon.cycle()
        assert daemon._idle_cycles == 1
        daemon.cycle()
        assert daemon._idle_cycles == 2


def test_signal_resets_idle_counter():
    with tempfile.TemporaryDirectory() as d:
        sensor = MockSensor([
            PerceptionFrame(source="empty", observations=[]),
            PerceptionFrame(source="fs", observations=["modified: x.py"]),
        ])
        engine = MagicMock()
        engine.awake = True
        engine.run.return_value = RunReport()
        daemon = _make_daemon(d, [sensor], engine)
        daemon.cycle()
        assert daemon._idle_cycles == 1
        daemon.cycle()
        assert daemon._idle_cycles == 0


def test_heartbeat_includes_idle_info():
    with tempfile.TemporaryDirectory() as d:
        hb = Path(d) / "hb.json"
        sensor = MockSensor([PerceptionFrame(source="empty", observations=[])])
        engine = MagicMock()
        engine.awake = True
        daemon = _make_daemon(d, [sensor], engine)
        daemon.cycle()
        data = json.loads(hb.read_text())
        assert data["idle_cycles"] == 1
        assert data["sensors_active"] == 1


def test_idle_returns_empty_run_report():
    with tempfile.TemporaryDirectory() as d:
        sensor = MockSensor([PerceptionFrame(source="empty", observations=[])])
        engine = MagicMock()
        engine.awake = True
        daemon = _make_daemon(d, [sensor], engine)
        result = daemon.cycle()
        assert isinstance(result, RunReport)
        assert result.cycles == 0
