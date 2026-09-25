"""v4.6.9: the doctor must ask the process itself, not guess from the filesystem.

False positive measured on this machine right after the 4.6.8 ship: a
process running the repo-editable 4.6.8 was reported as 4.6.7 because the
dist-info walk (c) climbs the executable's parents and returns the FIRST
conscio-*.dist-info it finds — and this machine carries several
(~/.local has one, the hermes venv has an ancient 2.7.0, uv-tools has the
current one). Any multi-install machine can hit this.

The fix adds level (b+): resolve the version by importing conscio with the
target process's own interpreter (its /proc/<pid>/exe), which gives the
process's REAL resolution order — sys.path of the venv it actually runs.
The walk (c) stays as the last resort, with dist-infos now SORTED so the
closest one (the venv's own) wins over random parents.

v4.7.3, measured 2026-09-24 in the post-update test of 4.7.2: 4 of 7 doctor
warnings were false, and the result changed with the doctor's own cwd.
  - the probe asked /proc/<pid>/exe, the symlink-RESOLVED base interpreter,
    which does not see the venv (uv tool, uvx archive, .venv); now it asks the
    interpreter as the process invoked it, with the process's sys.path[0],
    PYTHON* env and isolation flags;
  - the walk climbed a PYTHON exe's parents into the base install (uv's
    ~/.local/share/uv/python/... reached ~/.local) and trusted editable
    dist-infos, whose Version is the install-time one, not the loaded code;
  - a process started before the code it would import today was written
    (upgrade in place, editable repo after a bump) was invisible.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conscio.liaison import relay_cli
from conscio.liaison.relay_cli import (
    _detect_version_from_dist_info,
    _interpreter_invocation,
    _is_editable_dist_info,
    _is_python_exe,
    _Probe,
    _probe_env,
    _probe_interpreter,
    _process_interpreter,
    _sort_dist_infos,
    find_stale_processes,
)


def _fake_conscio(root: Path, version: str) -> Path:
    """A source tree whose `import conscio` answers `version`."""
    pkg = root / "conscio"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    return root


def _dist_info(site: Path, version: str, editable: bool = False) -> Path:
    di = site / f"conscio-{version}.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text(f"Name: conscio\nVersion: {version}\n", encoding="utf-8")
    if editable:
        (di / "direct_url.json").write_text(
            '{"url": "file:///src", "dir_info": {"editable": true}}', encoding="utf-8")
    return di


def _fake_proc(proc_root: Path, pid: int, args: list[str], exe: str,
               start_ticks: int | None = None, btime: int = 1_000_000) -> Path:
    entry = proc_root / str(pid)
    entry.mkdir(parents=True)
    (entry / "cmdline").write_bytes(b"\x00".join(a.encode() for a in args) + b"\x00")
    (entry / "comm").write_text(Path(args[0]).name + "\n", encoding="utf-8")
    (entry / "exe").symlink_to(exe)
    if start_ticks is not None:
        (proc_root / "stat").write_text(f"cpu 0 0\nbtime {btime}\n", encoding="utf-8")
        fields = ["S"] + ["0"] * 18 + [str(start_ticks)] + ["0"] * 5
        (entry / "stat").write_text(f"{pid} (python3) " + " ".join(fields), encoding="utf-8")
    return entry


class TestInterpreterResolution:
    def test_detects_version_of_our_own_interpreter(self):
        """Level (b+): importing with OUR interpreter returns OUR version."""
        ver = _probe_interpreter(Path(sys.executable)).version
        assert ver is not None
        # this test runs under the repo-editable install -> must be the package version
        import conscio
        assert ver == conscio.__version__

    def test_dead_interpreter_returns_none(self, tmp_path):
        assert _probe_interpreter(tmp_path / "no-such-python").version is None

    def test_interpreter_without_conscio_returns_none(self, tmp_path):
        fake = tmp_path / "fakepy"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        assert _probe_interpreter(fake).version is None


class TestDistInfoOrder:
    def test_closer_dist_info_wins(self, tmp_path):
        """Sorting: deeper paths (the venv's own site-packages) sort first."""
        far = tmp_path / "lib" / "python3.12" / "site-packages"
        near = tmp_path / "venv" / "lib" / "python3.14" / "site-packages"
        infos = [far / "conscio-1.0.0.dist-info",
                 near / "conscio-9.9.9.dist-info"]
        ordered = _sort_dist_infos(infos)
        assert ordered[0].name == "conscio-9.9.9.dist-info"


class TestProbeAsInvoked:
    """v4.7.3: the probe resolves `import conscio` the way the PROCESS did."""

    def test_process_sys_path0_wins(self, tmp_path):
        """`python -m` from the repo loads the repo, whatever is installed."""
        src = _fake_conscio(tmp_path / "src", "0.0.1")
        probe = _probe_interpreter(sys.executable, path0=str(src))
        assert probe.version == "0.0.1"
        assert probe.module_file == str(src / "conscio" / "__init__.py")
        assert probe.importable is True

    def test_target_pythonpath_is_honoured(self, tmp_path):
        src = _fake_conscio(tmp_path / "src", "0.0.1")
        env = _probe_env({"PYTHONPATH": str(src), "HOME": os.environ.get("HOME", "/")})
        assert _probe_interpreter(sys.executable, env=env).version == "0.0.1"

    def test_isolated_flag_ignores_pythonpath(self, tmp_path):
        src = _fake_conscio(tmp_path / "src", "0.0.1")
        env = _probe_env({"PYTHONPATH": str(src), "HOME": os.environ.get("HOME", "/")})
        assert _probe_interpreter(sys.executable, env=env, flags="I").version != "0.0.1"

    def test_probe_env_carries_only_interpreter_keys(self):
        env = _probe_env({"PYTHONPATH": "/p", "HOME": "/h", "SECRET_TOKEN": "x"})
        assert env["PYTHONPATH"] == "/p" and env["HOME"] == "/h"
        assert "SECRET_TOKEN" not in env

    def test_the_doctors_own_pythonpath_does_not_leak(self, tmp_path, monkeypatch):
        """The result must not depend on who runs the doctor, or from where."""
        src = _fake_conscio(tmp_path / "src", "0.0.1")
        monkeypatch.setenv("PYTHONPATH", str(src))
        assert "PYTHONPATH" not in _probe_env({"HOME": "/h"})

    def test_invocation_reproduces_sys_path0(self):
        assert _interpreter_invocation(["python3", "-m", "x"], "/cwd", None) == ("/cwd", "")
        assert _interpreter_invocation(["python3", "/a/b/s.py"], "/cwd", None) == ("/a/b", "")
        assert _interpreter_invocation(["python3", "s.py"], "/cwd", None) == ("/cwd", "")
        assert _interpreter_invocation(["python3", "-W", "ignore", "-m", "x"], "/cwd", None) == ("/cwd", "")
        # -I / -P / PYTHONSAFEPATH: no sys.path[0] at all
        assert _interpreter_invocation(["python3", "-I", "-m", "x"], "/cwd", None) == (None, "I")
        assert _interpreter_invocation(["python3", "-P", "-m", "x"], "/cwd", None) == (None, "P")
        assert _interpreter_invocation(["python3", "-m", "x"], "/cwd", {"PYTHONSAFEPATH": "1"})[0] is None

    def test_venv_interpreter_beats_resolved_exe(self, tmp_path):
        """exe is the base interpreter; cmdline[0] is the venv's, which sees the venv."""
        venv_py = tmp_path / "venv" / "bin" / "python3"
        venv_py.parent.mkdir(parents=True)
        venv_py.symlink_to(sys.executable)
        assert _process_interpreter([str(venv_py), "-m", "x"], sys.executable, None, None) == str(venv_py)
        # bare name: resolved on the PROCESS's PATH, not the doctor's
        env = {"PATH": str(venv_py.parent)}
        assert _process_interpreter(["python3", "-m", "x"], sys.executable, None, env) == str(venv_py)

    def test_non_python_is_never_probed(self):
        """`claude -c` is --continue; zcode/antigravity daemons fork and outlive the kill."""
        assert _process_interpreter(["claude", "-p"], "/usr/bin/claude", None, None) is None
        assert not _is_python_exe("/opt/ZCode/zcode")
        assert not _is_python_exe("/usr/bin/python3.12 (deleted)")  # gone: cannot run


class TestDistInfoFallback:
    def test_editable_dist_info_is_not_evidence(self, tmp_path):
        """~/.local said 4.7.1 and the repo .venv said 3.8.2 while both loaded 4.7.2."""
        site = tmp_path / "lib" / "python3.12" / "site-packages"
        di = _dist_info(site, "3.8.2", editable=True)
        assert _is_editable_dist_info(di)
        assert _detect_version_from_dist_info([tmp_path / "bin" / "x"]) is None

    def test_regular_dist_info_still_answers(self, tmp_path):
        site = tmp_path / "lib" / "python3.12" / "site-packages"
        di = _dist_info(site, "1.0.0")
        assert not _is_editable_dist_info(di)
        assert _detect_version_from_dist_info([tmp_path / "bin" / "x"]) == "1.0.0"


class TestFindStaleProcesses:
    @pytest.mark.parametrize("deleted", [False, True])
    def test_python_exe_parents_are_not_walked(self, tmp_path, deleted):
        """uv's base interpreter lives under a tree with an unrelated dist-info."""
        base = tmp_path / "uvpython"
        _dist_info(base / "lib" / "python3.12" / "site-packages", "1.0.0")
        interp = base / "bin" / "python3.12"
        interp.parent.mkdir(parents=True)
        interp.write_text("")  # present but not runnable: the probe gets nothing
        exe = str(interp) + (" (deleted)" if deleted else "")
        _fake_proc(tmp_path / "proc", 4242, ["python3", "-m", "conscio.liaison.reactor"], exe)
        assert find_stale_processes("4.7.2", proc_root=tmp_path / "proc") == []

    def test_non_python_launcher_still_uses_the_walk(self, tmp_path):
        tool = tmp_path / "tool"
        _dist_info(tool / "lib" / "python3.12" / "site-packages", "1.0.0")
        launcher = tool / "bin" / "conscio-launcher"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("")
        _fake_proc(tmp_path / "proc", 4243, ["conscio-launcher"], str(launcher))
        stale = find_stale_processes("4.7.2", proc_root=tmp_path / "proc")
        assert [(s["pid"], s["running_version"], s["reason"]) for s in stale] == [
            (4243, "1.0.0", "older_version")]

    @pytest.mark.parametrize("offset, flagged", [(+100, True), (+1, False), (-100, False)])
    def test_process_older_than_its_code_on_disk(self, tmp_path, monkeypatch, offset, flagged):
        """Same version on disk, but written after the process started: it runs old code."""
        clk = os.sysconf("SC_CLK_TCK")
        btime, ticks = 1_000_000, 50 * clk
        started = btime + ticks / clk
        code = _fake_conscio(tmp_path / "src", "4.7.2") / "conscio" / "__init__.py"
        os.utime(code, (started + offset, started + offset))
        monkeypatch.setattr(relay_cli, "_probe_interpreter",
                            lambda exe, **kw: _Probe("4.7.2", str(code), True))
        _fake_proc(tmp_path / "proc", 4244, ["python3", "-m", "conscio.liaison.reactor"],
                   sys.executable, start_ticks=ticks, btime=btime)
        stale = find_stale_processes("4.7.2", proc_root=tmp_path / "proc")
        if not flagged:
            assert stale == []
            return
        assert len(stale) == 1
        sp = stale[0]
        assert sp["reason"] == "code_newer_than_process"
        assert sp["running_version"] == "<4.7.2"
        assert sp["module_file"] == str(code)
        assert sp["started"] == started

    def test_non_importable_process_not_judged_by_disk(self, tmp_path, monkeypatch):
        """v4.7.3: the interpreter answered that conscio is NOT importable there
        (a wrapper/watchdog, not Conscio) — a reachable dist-info on disk must
        not resurrect the process as stale Conscio code."""
        site = tmp_path / "libs" / "python3.12" / "site-packages"
        _dist_info(site, "1.0.0")  # older than the target: it WOULD look stale
        _fake_proc(tmp_path / "proc", 4245,
                   [sys.executable, "-m", "conscio.liaison.reactor", str(site)],
                   sys.executable)
        monkeypatch.setattr(relay_cli, "_probe_interpreter",
                            lambda exe, **kw: _Probe(importable=False))
        assert find_stale_processes("4.7.2", proc_root=tmp_path / "proc") == []
