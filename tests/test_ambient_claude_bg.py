# tests/test_ambient_claude_bg.py
import pathlib
import subprocess

import pytest

from conscio.ambient import connectors, doctor, node

FIX = pathlib.Path(__file__).parent / "fixtures" / "claude_bg"


def _fix(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_bg_session_id_from_the_probe_capture():
    assert connectors.parse_bg_session_id(_fix("bg_start.txt")) == _fix("bg_start.session_id").strip()
    assert connectors.parse_bg_session_id("no id here") is None


def test_parse_agents_real_captures():
    working = connectors.parse_agents(_fix("agents_working.json"))
    assert working is not None
    assert working["c8ce076a"] is True
    after = connectors.parse_agents(_fix("agents_after_done.json"))
    assert after is not None
    assert after["f10bd885"] is False          # done stays listed, but is not activity
    assert after["93e12ae9"] is None           # blocked: cannot tell (A-3)
    assert len(after) == 2                     # the interactive entry has no id: skipped
    everything = connectors.parse_agents(_fix("agents_all.json"))
    assert everything is not None
    assert everything["a80b2948"] is False     # failed
    assert connectors.parse_agents("[]") == {}
    assert connectors.parse_agents("") == {}


@pytest.mark.parametrize("text", ["garbage", '{"id": "x"}', "[1, 2]"])
def test_parse_agents_unknown_format_is_none(text):
    assert connectors.parse_agents(text) is None


def test_parse_agents_plain_listing_without_tty_is_none():   # A3: the non-JSON form
    assert connectors.parse_agents(_fix("agents_plain_no_tty.txt")) is None


def test_unknown_state_is_none_not_false():
    assert connectors.parse_agents('[{"id": "ab12cd34", "state": "paused"}]') == {"ab12cd34": None}


def test_argv_isolates_in_a_scope_and_keeps_prompt_bytes():
    prompt = node.WAKE_PROMPT.format(task_id=42)
    argv = connectors.ClaudeBg().argv(entry={"model": "m"}, prompt=prompt)
    assert argv[:5] == ["systemd-run", "--user", "--scope", "--collect", "--quiet"]
    assert argv[5:] == ["claude", "--bg", "--model", "m", prompt]
    assert argv.count(prompt) == 1                                  # I8


def _run_writing(rc: int, out: str = "", err: str = ""):
    def run(argv, **kw):
        assert kw["stdin"] is subprocess.DEVNULL and "capture_output" not in kw   # A-4
        kw["stdout"].write(out.encode())
        kw["stderr"].write(err.encode())
        return subprocess.CompletedProcess(argv, rc)
    return run


def test_spawn_returns_the_short_id():
    got = connectors.ClaudeBg(run=_run_writing(0, _fix("bg_start.txt"))).spawn(
        entry={}, prompt="p", env={}, cwd="/")
    assert got.session_id == "c8ce076a" and got.pid is None


@pytest.mark.parametrize(("rc", "out", "err", "reason"), [
    (1, "", _fix("bg_untrusted.stderr"), "spawn_error"),            # A-2: $HOME refused
    (1, "", "API Error: 429 rate_limit_error", "rate_limited"),
    (0, "started, but no id line", "", "spawn_error"),
])
def test_spawn_failures_map_to_reasons(rc, out, err, reason):
    with pytest.raises(connectors.SpawnFailed) as caught:
        connectors.ClaudeBg(run=_run_writing(rc, out, err)).spawn(
            entry={}, prompt="p", env={}, cwd="/")
    assert caught.value.reason == reason
    if rc == 1 and reason == "spawn_error":
        assert "not trusted" in str(caught.value)        # the cause reaches wake_failed


def test_spawn_timeout_and_missing_binary():
    def timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 60)
    def missing(argv, **kw):
        raise FileNotFoundError("systemd-run")
    for run, reason in ((timeout, "timeout"), (missing, "spawn_error")):
        with pytest.raises(connectors.SpawnFailed) as caught:
            connectors.ClaudeBg(run=run).spawn(entry={}, prompt="p", env={}, cwd="/")
        assert caught.value.reason == reason


def _run_listing(stdout: str, rc: int = 0):
    def run(argv, **kw):
        assert argv[-2:] == ["agents", "--json"]
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")
    return run


def test_is_active_reads_the_table():
    listing = _fix("agents_after_done.json")
    bg = connectors.ClaudeBg(run=_run_listing(listing))
    assert bg.is_active("f10bd885") is False
    assert bg.is_active("93e12ae9") is None
    assert bg.is_active("deadbeef") is False                  # absent
    assert connectors.ClaudeBg(run=_run_listing(_fix("agents_working.json"))).is_active("c8ce076a") is True


def test_is_active_unknown_on_error_exit_or_garbage():
    def boom(argv, **kw):
        raise OSError("claude missing")
    assert connectors.ClaudeBg(run=boom).is_active("x") is None
    assert connectors.ClaudeBg(run=_run_listing("", rc=1)).is_active("x") is None
    assert connectors.ClaudeBg(run=_run_listing("garbage")).is_active("x") is None


def test_stop_calls_claude_stop():
    called = []
    def run_stop(argv, **kw):
        called.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0)
    connectors.ClaudeBg(run=run_stop).stop("c8ce076a")
    assert called == [(["claude", "stop", "c8ce076a"], {"capture_output": True, "text": True, "timeout": 15, "check": False})]


def test_registered():
    assert isinstance(connectors.CONNECTORS["claude-bg"], connectors.ClaudeBg)


def test_doctor_units_and_claude_success():
    def run_cmd(argv, **kw):
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 0, stdout="unit1.service loaded active running\n", stderr="")
        if argv[0] == "claude":
            return subprocess.CompletedProcess(argv, 0, stdout="claude 2.1.0\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    assert doctor.check_units(run_cmd) == "units: unit1.service loaded active running"
    assert doctor.check_claude(run_cmd) == "claude: claude 2.1.0"


def test_doctor_units_none_when_empty():
    def run_cmd(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout="   \n", stderr="")

    assert doctor.check_units(run_cmd) == "units: none"


def test_doctor_units_and_claude_failure_and_exceptions_never_raise():
    def run_err(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="failed to connect to bus")

    assert doctor.check_units(run_err) == "units: unknown (failed to connect to bus)"
    assert doctor.check_claude(run_err) == "claude: unknown (failed to connect to bus)"

    def run_timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 5)

    assert "unknown" in doctor.check_units(run_timeout)
    assert "unknown" in doctor.check_claude(run_timeout)

    def run_oserror(argv, **kw):
        raise FileNotFoundError("command not found")

    assert "unknown" in doctor.check_units(run_oserror)
    assert "unknown" in doctor.check_claude(run_oserror)


def test_doctor_run_includes_units_and_claude(tmp_path):
    def run_cmd(argv, **kw):
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 0, stdout="unit1\n", stderr="")
        if argv[0] == "claude":
            return subprocess.CompletedProcess(argv, 0, stdout="v1.0\n", stderr="")
        raise AssertionError(f"unexpected command: {argv}")

    lines = doctor.run(root=tmp_path, proc_root=tmp_path, now=0.0, run_cmd=run_cmd)
    assert any(line == "units: unit1" for line in lines)
    assert any(line == "claude: v1.0" for line in lines)
