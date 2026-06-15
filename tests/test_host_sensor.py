"""v1.5 HostSensor — read-only host facts (Risk.LOW). Every probe is guarded:
a failing probe degrades to an omitted line, never an exception; a non-Linux /
hostile env yields a reduced frame, never a crash. No outbound network.
"""
import socket

from conscio.perception import HostSensor, PerceptionFrame
from conscio.risk import Risk


def test_perceive_returns_host_frame_with_signals():
    frame = HostSensor().perceive()
    assert isinstance(frame, PerceptionFrame)
    assert frame.source == "host"
    assert frame.observations                       # non-empty on a real host
    # disk is portable (shutil) -> disk_pct is present on any machine.
    assert "disk_pct" in frame.signals
    assert isinstance(frame.signals["disk_pct"], float)


def test_risk_is_low():
    assert HostSensor.risk is Risk.LOW


def test_to_world_state_roundtrips_into_reflect_string():
    ws = HostSensor().perceive().to_world_state()
    assert ws.startswith("[host]")            # the deterministic v1.3 builder


def test_all_probes_failing_returns_reduced_frame_never_raises(monkeypatch):
    # Simulate a hostile/non-Linux env: every probe raises -> frame still valid.
    import conscio.perception.host_sensor as hs

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(hs.os, "getloadavg", boom, raising=False)
    monkeypatch.setattr(hs.shutil, "disk_usage", boom)
    monkeypatch.setattr(hs.subprocess, "run", boom)
    monkeypatch.setattr(hs, "_read_meminfo", dict)   # mem omitted
    frame = HostSensor().perceive()
    assert isinstance(frame, PerceptionFrame)
    assert frame.source == "host"
    assert frame.observations                 # at least the "no probes" line
    assert frame.signals == {}                # nothing numeric survived


def test_single_probe_failure_is_isolated(monkeypatch):
    # loadavg raises but disk still reports -> partial frame, no exception.
    import conscio.perception.host_sensor as hs

    def boom(*a, **k):
        raise OSError("no loadavg")

    monkeypatch.setattr(hs.os, "getloadavg", boom, raising=False)
    frame = HostSensor().perceive()
    assert "disk_pct" in frame.signals
    assert "load" not in frame.signals


def test_service_liveness_default_off_makes_no_socket(monkeypatch):
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        raise OSError()

    monkeypatch.setattr(socket, "create_connection", spy)
    HostSensor().perceive()                   # default services=() -> no calls
    assert called["n"] == 0


def test_service_liveness_reports_open_and_closed_ports():
    # Bind a real loopback listener -> "up"; an unbound low port -> "down".
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    up_port = srv.getsockname()[1]
    try:
        frame = HostSensor(services=[up_port, 1]).perceive()
        text = "\n".join(frame.observations)
        assert str(up_port) in text and "up" in text
        assert "down" in text                 # port 1 is not listening
    finally:
        srv.close()
