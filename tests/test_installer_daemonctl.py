import os
import pytest
from conscio.installer import daemonctl


@pytest.fixture(autouse=True)
def _base(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSCIO_BASE", str(tmp_path / ".conscio"))


def test_pid_file_path():
    assert daemonctl.pid_file("h").name == "h.pid"


def test_not_running_when_no_pidfile():
    assert daemonctl.is_running("h") is False
    assert daemonctl.stop("h") is False


def test_stale_pidfile_not_running():
    daemonctl.pid_file("h").parent.mkdir(parents=True, exist_ok=True)
    daemonctl.pid_file("h").write_text("999999")        # almost certainly dead
    assert daemonctl.is_running("h") is False


def test_live_but_wrong_cmdline_not_running(monkeypatch):
    # our own PID is alive but is pytest, not conscio-daemon -> not "running"
    daemonctl.pid_file("h").parent.mkdir(parents=True, exist_ok=True)
    daemonctl.pid_file("h").write_text(str(os.getpid()))
    monkeypatch.setattr(daemonctl, "_cmdline", lambda pid: "python pytest")
    assert daemonctl.is_running("h") is False


def test_live_matching_cmdline_is_running(monkeypatch):
    daemonctl.pid_file("h").parent.mkdir(parents=True, exist_ok=True)
    daemonctl.pid_file("h").write_text(str(os.getpid()))
    monkeypatch.setattr(daemonctl, "_cmdline",
                        lambda pid: "conscio daemon --storage x")
    assert daemonctl.is_running("h") is True


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(daemonctl, "is_running", lambda slug: True)
    monkeypatch.setattr(daemonctl, "_read_pid", lambda slug: 4242)
    assert daemonctl.start("h", extra_args=[]) == 4242    # no second spawn
