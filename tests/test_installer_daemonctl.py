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


class _FakeProc:
    def __init__(self, pid=777, rc=None):
        self.pid = pid
        self.returncode = rc

    def poll(self):
        return self.returncode


def test_start_sets_perhost_vault_env(monkeypatch):
    from conscio.installer import spaces
    seen = {}

    def fake_popen(cmd, **kw):
        seen["env"] = kw.get("env")
        return _FakeProc()

    monkeypatch.setattr(daemonctl.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(daemonctl.time, "sleep", lambda s: None)
    assert daemonctl.start("h", extra_args=[]) == 777
    assert seen["env"] is not None                        # env explicitly passed
    assert seen["env"]["CONSCIO_VAULT_DIR"] == str(spaces.vault_dir("h"))


def test_start_reports_immediate_death(monkeypatch):
    monkeypatch.setattr(daemonctl.subprocess, "Popen",
                        lambda cmd, **kw: _FakeProc(rc=2))
    monkeypatch.setattr(daemonctl.time, "sleep", lambda s: None)
    with pytest.raises(daemonctl.DaemonStartError):
        daemonctl.start("h", extra_args=["--bad-flag"])
    assert not daemonctl.pid_file("h").exists()           # no pidfile for a corpse


def test_start_missing_binary_raises_clean(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("conscio")

    monkeypatch.setattr(daemonctl.subprocess, "Popen", boom)
    with pytest.raises(daemonctl.DaemonStartError):
        daemonctl.start("h", extra_args=[])


def test_stop_keeps_pidfile_on_eperm(monkeypatch):
    daemonctl.pid_file("h").parent.mkdir(parents=True, exist_ok=True)
    daemonctl.pid_file("h").write_text(str(os.getpid()))
    monkeypatch.setattr(daemonctl, "is_running", lambda slug: True)

    def eperm(pid, sig):
        raise PermissionError("not ours")

    monkeypatch.setattr(daemonctl.os, "kill", eperm)
    assert daemonctl.stop("h") is False                   # daemon still alive
    assert daemonctl.pid_file("h").exists()               # pidfile NOT dropped
