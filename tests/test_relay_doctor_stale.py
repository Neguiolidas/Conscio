"""Tests for detecting stale live processes running older Conscio versions (Relay #224)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from conscio.liaison import directory, relay_cli
from conscio.liaison.relay_cli import (
    _parse_version,
    find_stale_processes,
)


@pytest.fixture(autouse=True)
def _relay_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))


def _make_proc_process(
    proc_root: Path,
    pid: int,
    cmdline_args: list[str],
    comm: str = "",
    exe_target: Path | str | None = None,
) -> Path:
    p = proc_root / str(pid)
    p.mkdir(parents=True, exist_ok=True)
    if comm:
        (p / "comm").write_text(f"{comm}\n", encoding="utf-8")
    if cmdline_args:
        encoded = b"\x00".join(arg.encode("utf-8") for arg in cmdline_args) + b"\x00"
        (p / "cmdline").write_bytes(encoded)
    if exe_target is not None:
        exe_path = p / "exe"
        if isinstance(exe_target, Path) and exe_target.exists():
            exe_path.symlink_to(exe_target)
        else:
            try:
                exe_path.symlink_to(str(exe_target))
            except OSError:
                exe_path.write_text(str(exe_target), encoding="utf-8")
    return p


def test_parse_version_comparisons():
    assert _parse_version("4.6.7") < _parse_version("4.6.8")
    assert not (_parse_version("4.6.8") < _parse_version("4.6.8"))
    assert not (_parse_version("4.7.0") < _parse_version("4.6.8"))
    assert _parse_version("4.5.4") < _parse_version("4.6.8")
    assert _parse_version("4.6") == _parse_version("4.6.0")
    assert _parse_version("v4.6.7") < _parse_version("4.6.8")
    assert _parse_version("4.6.7.1") < _parse_version("4.6.8")


def test_detect_report_version_flag_space(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=101,
        cmdline_args=["python3", "-u", "-m", "conscio.liaison.reactor", "--report-version", "4.6.6"],
        comm="python3",
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert len(stale) == 1
    assert stale[0]["pid"] == 101
    assert stale[0]["name"] == "python3"
    assert stale[0]["running_version"] == "4.6.6"
    assert stale[0]["installed_version"] == "4.6.8"


def test_detect_report_version_flag_equals(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=102,
        cmdline_args=["conscio-reactor", "--report-version=4.6.5"],
        comm="conscio-reactor",
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert len(stale) == 1
    assert stale[0]["pid"] == 102
    assert stale[0]["name"] == "conscio-reactor"
    assert stale[0]["running_version"] == "4.6.5"
    assert stale[0]["installed_version"] == "4.6.8"


def test_detect_uvx_from_flag_double_equals(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=201,
        cmdline_args=["uvx", "--from", "conscio==4.6.5", "conscio-mcp", "--storage", "/tmp/space"],
        comm="uvx",
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert len(stale) == 1
    assert stale[0]["pid"] == 201
    assert stale[0]["name"] == "uvx"
    assert stale[0]["running_version"] == "4.6.5"
    assert stale[0]["installed_version"] == "4.6.8"


def test_detect_uvx_from_flag_at(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=202,
        cmdline_args=["/home/user/.cargo/bin/uvx", "--from=conscio@4.6.4", "conscio"],
        comm="uvx",
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert len(stale) == 1
    assert stale[0]["pid"] == 202
    assert stale[0]["running_version"] == "4.6.4"
    assert stale[0]["installed_version"] == "4.6.8"


def test_detect_dist_info_from_parent_hierarchy(tmp_path):
    fake_venv = tmp_path / "custom_venv"
    site_packages = fake_venv / "lib" / "python3.11" / "site-packages"
    dist_info = site_packages / "conscio-4.6.6.dist-info"
    dist_info.mkdir(parents=True)
    fake_bin = fake_venv / "bin"
    fake_bin.mkdir(parents=True)
    fake_py = fake_bin / "python3"
    fake_py.write_text("#!/bin/sh\n", encoding="utf-8")

    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=301,
        cmdline_args=[str(fake_py), "-m", "conscio.liaison.reactor"],
        comm="python3",
        exe_target=fake_py,
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert len(stale) == 1
    assert stale[0]["pid"] == 301
    assert stale[0]["running_version"] == "4.6.6"
    assert stale[0]["installed_version"] == "4.6.8"


def test_detect_dist_info_with_metadata_file(tmp_path):
    fake_venv = tmp_path / "tool_venv"
    site_packages = fake_venv / "lib" / "python3.12" / "site-packages"
    dist_info = site_packages / "conscio-4.6.5.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: conscio\nVersion: 4.6.5\n", encoding="utf-8")
    fake_bin = fake_venv / "bin"
    fake_bin.mkdir(parents=True)
    fake_conscio = fake_bin / "conscio"
    fake_conscio.write_text("#!/bin/sh\n", encoding="utf-8")

    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=302,
        cmdline_args=[str(fake_conscio), "relay", "reactor"],
        comm="conscio",
        exe_target=fake_conscio,
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert len(stale) == 1
    assert stale[0]["pid"] == 302
    assert stale[0]["running_version"] == "4.6.5"
    assert stale[0]["installed_version"] == "4.6.8"


def test_ignores_non_conscio_processes(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(proc_root, pid=401, cmdline_args=["bash", "-i"], comm="bash")
    _make_proc_process(proc_root, pid=402, cmdline_args=["nginx", "-g", "daemon off;"], comm="nginx")
    _make_proc_process(proc_root, pid=403, cmdline_args=["python3", "server.py"], comm="python3")
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert stale == []


def test_ignores_newer_or_equal_version_processes(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=501,
        cmdline_args=["conscio", "--report-version", "4.6.8"],
        comm="conscio",
    )
    _make_proc_process(
        proc_root,
        pid=502,
        cmdline_args=["conscio", "--report-version", "4.7.0"],
        comm="conscio",
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert stale == []


def test_excludes_self_pid(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=os.getpid(),
        cmdline_args=["conscio", "--report-version", "4.6.0"],
        comm="conscio",
    )
    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert stale == []


def test_cmd_doctor_prints_aviso(tmp_path, capsys):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=999,
        cmdline_args=["python3", "-m", "conscio.liaison.reactor", "--report-version", "4.6.6"],
        comm="conscio-reactor",
    )
    directory.publish({"instance_id": "test-agent",
                       "spool": str(directory.spool_dir("test-agent")), "url": ""})

    rc = relay_cli.main(["doctor", "--id", "test-agent", "--proc-root", str(proc_root), "--installed-version", "4.6.8"])
    out = capsys.readouterr()
    assert rc == 0
    expected_aviso = (
        "AVISO: processo 999 (conscio-reactor) roda versao 4.6.6 < instalada 4.6.8, "
        "reinicie apos upgrade para nao apagar campos novos"
    )
    assert expected_aviso in out.out


def test_cmd_doctor_no_aviso_when_healthy(tmp_path, capsys):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=888,
        cmdline_args=["python3", "-m", "conscio.liaison.reactor", "--report-version", "4.6.8"],
        comm="conscio-reactor",
    )
    directory.publish({"instance_id": "test-agent",
                       "spool": str(directory.spool_dir("test-agent")), "url": ""})

    rc = relay_cli.main(["doctor", "--id", "test-agent", "--proc-root", str(proc_root), "--installed-version", "4.6.8"])
    out = capsys.readouterr()
    assert rc == 0
    assert "AVISO:" not in out.out


def test_resilience_to_broken_proc_entries(tmp_path):
    proc_root = tmp_path / "proc"
    # Process with missing cmdline and comm
    (proc_root / "701").mkdir(parents=True)
    # Non-pid directory in proc
    (proc_root / "sys").mkdir(parents=True)
    (proc_root / "net").mkdir(parents=True)
    # Empty cmdline
    (proc_root / "702").mkdir(parents=True)
    (proc_root / "702" / "cmdline").write_bytes(b"")

    stale = find_stale_processes(installed_version="4.6.8", proc_root=proc_root)
    assert stale == []


def test_default_installed_version(tmp_path):
    proc_root = tmp_path / "proc"
    _make_proc_process(
        proc_root,
        pid=123,
        cmdline_args=["conscio", "--report-version", "0.0.1"],
        comm="conscio",
    )
    stale = find_stale_processes(proc_root=proc_root)
    assert len(stale) == 1
    assert stale[0]["pid"] == 123
    assert stale[0]["running_version"] == "0.0.1"
    assert stale[0]["installed_version"] != ""
