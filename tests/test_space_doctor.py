"""Tests for Space Doctor diagnostics (D1-D5, refusal markers, and D5 actionable advice).

Covers:
- D1: Orphan spaces under ~/.conscio/instances/ (id, size, mtime), ignoring regular files and `.` prefix
- D2: Downgrade phantom (durable space with tombstone + plugin space with divergent id)
- D3: Orphan tombstone (migrated-from.json pointing to non-existent plugin path)
- D4: Deferred migration in B0 with active blocking PIDs
- D5: Stale migration lock (.migrating-<slug>) with dead PID suggesting explicit `rm` command
- Refusal markers: space-refused.json with registered state, reason, and age
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conscio.installer.durable import known_plugin_data_dirs
from conscio.installer.spaces import INSTANCES_ROOT
from conscio.liaison import directory, relay_cli


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_base = fake_home / ".conscio"
    fake_base.mkdir(parents=True, exist_ok=True)
    fake_relay = fake_base / "relay"
    fake_relay.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CONSCIO_BASE", str(fake_base))
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(fake_relay))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("ZCODE_PLUGIN_DATA", raising=False)
    for k in list(os.environ.keys()):
        if k.startswith("CONSCIO_") and k not in ("CONSCIO_BASE", "CONSCIO_RELAY_ROOT"):
            monkeypatch.delenv(k, raising=False)


def _make_proc(proc_root: Path, pid: int, cmdline_args: list[str]) -> Path:
    p = proc_root / str(pid)
    p.mkdir(parents=True, exist_ok=True)
    if cmdline_args:
        raw = b"\x00".join(arg.encode("utf-8") for arg in cmdline_args) + b"\x00"
        (p / "cmdline").write_bytes(raw)
    return p


def test_known_plugin_data_dirs_discovery(tmp_path):
    """Test known_plugin_data_dirs resolves env vars and standard filesystem defaults."""
    fake_home = tmp_path / "home"
    claude_default = fake_home / ".claude" / "plugins" / "data" / "conscio-conscio"
    zcode_default = fake_home / ".zcode" / "cli" / "plugins" / "data" / "conscio@conscio"

    # When none exist on disk and only_existing=True
    assert known_plugin_data_dirs(env={}, home=fake_home, only_existing=True) == []

    # When only_existing=False
    all_candidates = known_plugin_data_dirs(env={}, home=fake_home, only_existing=False)
    assert claude_default in all_candidates
    assert zcode_default in all_candidates

    # When created on disk
    claude_default.mkdir(parents=True, exist_ok=True)
    existing = known_plugin_data_dirs(env={}, home=fake_home, only_existing=True)
    assert existing == [claude_default]

    # With environment variable override
    custom_plugin = tmp_path / "custom_plugin"
    custom_plugin.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_PLUGIN_DATA": str(custom_plugin)}
    with_env = known_plugin_data_dirs(env=env, home=fake_home, only_existing=True)
    assert custom_plugin in with_env
    assert claude_default in with_env


def test_known_plugin_dirs_ignores_unexpanded_and_relative(tmp_path, monkeypatch):
    """Adendo 2 ao G16: known_plugin_data_dirs ignores unexpanded variables and relative paths

    even if ghost directories exist on disk in cwd.
    """
    fake_home = tmp_path / "fakehome"
    monkeypatch.chdir(tmp_path)

    # Create ghost directory with literal unexpanded variable name in cwd
    ghost_unexpanded = tmp_path / "${CLAUDE_PLUGIN_DATA}"
    ghost_unexpanded.mkdir(parents=True, exist_ok=True)

    # Create relative ghost directory in cwd
    ghost_relative = tmp_path / "relative_plugin_data"
    ghost_relative.mkdir(parents=True, exist_ok=True)

    # Create a valid absolute directory
    valid_dir = tmp_path / "valid_plugin_data"
    valid_dir.mkdir(parents=True, exist_ok=True)

    env = {
        "CLAUDE_PLUGIN_DATA": "${CLAUDE_PLUGIN_DATA}",
        "ZCODE_PLUGIN_DATA": "relative_plugin_data",
    }

    # Should ignore both ghost directories despite only_existing=True and them existing in cwd!
    dirs = known_plugin_data_dirs(env=env, home=fake_home, only_existing=True)
    assert ghost_unexpanded not in dirs
    assert ghost_relative not in dirs
    assert dirs == []

    # Also test valid absolute path is still accepted
    env["CLAUDE_PLUGIN_DATA"] = str(valid_dir)
    dirs = known_plugin_data_dirs(env=env, home=fake_home, only_existing=True)
    assert dirs == [valid_dir]


def test_stale_lock_listed_not_cleared(tmp_path, capsys):
    """Spec Named Test 27: D5 stale migration lock with dead PID prints executable `rm`

    and NEVER clears or modifies the lock file automatically.
    """
    proc_root = tmp_path / "fake_proc"
    proc_root.mkdir(parents=True, exist_ok=True)

    inst_root = INSTANCES_ROOT()
    inst_root.mkdir(parents=True, exist_ok=True)

    lock_file = inst_root / ".migrating-claude-code"
    lock_file.write_text(
        json.dumps({"schema": 1, "pid": 99999, "started_ts": 123456789.0}),
        encoding="utf-8",
    )

    rc = relay_cli.main(["doctor", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out
    assert rc == 0

    # Must suggest the rm command for operator
    assert "rm " in out
    assert str(lock_file) in out
    assert "99999" in out

    # Must NEVER delete the file automatically
    assert lock_file.exists()


def test_active_lock_not_suggesting_rm(tmp_path, capsys):
    """When the PID recorded in .migrating-<slug> is alive, it's an active migration, not stale."""
    proc_root = tmp_path / "fake_proc"
    proc_root.mkdir(parents=True, exist_ok=True)
    _make_proc(proc_root, 1234, ["conscio", "space", "migrate"])

    inst_root = INSTANCES_ROOT()
    inst_root.mkdir(parents=True, exist_ok=True)

    lock_file = inst_root / ".migrating-active"
    lock_file.write_text(
        json.dumps({"schema": 1, "pid": 1234, "started_ts": 123456789.0}),
        encoding="utf-8",
    )

    rc = relay_cli.main(["doctor", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out
    assert rc == 0

    assert f"rm {lock_file}" not in out
    assert lock_file.exists()


def test_d1_orphan_space_reported(tmp_path, capsys):
    """D1: Orphan spaces in ~/.conscio/instances/ (id, size, mtime)

    ignoring regular files and entries starting with `.`.
    """
    proc_root = tmp_path / "fake_proc"
    proc_root.mkdir(parents=True, exist_ok=True)

    inst_root = INSTANCES_ROOT()
    inst_root.mkdir(parents=True, exist_ok=True)

    # 1. An orphan space directory
    orphan_dir = inst_root / "freebuff"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "orphan-fb-uuid-1234"}),
        encoding="utf-8",
    )
    (orphan_dir / "conscio.db").write_bytes(b"x" * 2048)

    # 2. Regular file that must be IGNORED by D1
    regular_file = inst_root / "notes.txt"
    regular_file.write_text("just a note", encoding="utf-8")

    # 3. Dot-prefixed entry (.minting-xxx) that must be IGNORED by D1
    dot_dir = inst_root / ".minting-test"
    dot_dir.mkdir(parents=True, exist_ok=True)

    rc = relay_cli.main(["doctor", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "freebuff" in out
    assert "orphan-fb-uuid-1234" in out
    # notes.txt and .minting-test must NOT be listed as orphan spaces
    assert "notes.txt" not in out
    assert ".minting-test" not in out


def test_d2_downgrade_phantom_reported(tmp_path, capsys):
    """D2: Downgrade phantom (durable space with tombstone + plugin space with divergent id).

    Doctor lists both evidences and suggests `conscio relay forget <phantom-id>`.
    Strict read-only: no files are altered.
    """
    proc_root = tmp_path / "fake_proc"
    proc_root.mkdir(parents=True, exist_ok=True)

    fake_home = tmp_path / "fakehome"
    plugin_dir = fake_home / ".claude" / "plugins" / "data" / "conscio-conscio"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "phantom-id-5678"}),
        encoding="utf-8",
    )

    inst_root = INSTANCES_ROOT()
    durable_dir = inst_root / "claude-code"
    durable_dir.mkdir(parents=True, exist_ok=True)
    (durable_dir / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "durable-id-1234"}),
        encoding="utf-8",
    )
    (durable_dir / "migrated-from.json").write_text(
        json.dumps({"schema": 1, "origin": str(plugin_dir), "slug": "claude-code"}),
        encoding="utf-8",
    )

    rc = relay_cli.main(["doctor", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "phantom-id-5678" in out
    assert "durable-id-1234" in out
    assert "conscio relay forget phantom-id-5678" in out
    # Read-only check
    assert (plugin_dir / "instance.json").exists()
    assert (durable_dir / "instance.json").exists()


def test_d3_orphan_tombstone_reported(tmp_path, capsys):
    """D3: Orphan tombstone (migrated-from.json pointing to non-existent plugin path).

    Informational post-migration check.
    """
    proc_root = tmp_path / "fake_proc"
    proc_root.mkdir(parents=True, exist_ok=True)

    inst_root = INSTANCES_ROOT()
    durable_dir = inst_root / "zcode"
    durable_dir.mkdir(parents=True, exist_ok=True)
    (durable_dir / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "zcode-uuid-9999"}),
        encoding="utf-8",
    )
    non_existent = tmp_path / "wiped_plugin_dir"
    (durable_dir / "migrated-from.json").write_text(
        json.dumps({"schema": 1, "origin": str(non_existent), "slug": "zcode"}),
        encoding="utf-8",
    )

    rc = relay_cli.main(["doctor", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "migrated-from.json" in out or "lapide orfa" in out or "lápide órfã" in out
    assert str(non_existent) in out


def test_d4_deferred_migration_reported(tmp_path, capsys):
    """D4: Deferred migration in B0 with active blocking PIDs."""
    proc_root = tmp_path / "fake_proc"
    proc_root.mkdir(parents=True, exist_ok=True)

    fake_home = tmp_path / "fakehome"
    plugin_dir = fake_home / ".claude" / "plugins" / "data" / "conscio-conscio"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    legacy_space = plugin_dir / "space"
    legacy_space.mkdir(parents=True, exist_ok=True)
    (legacy_space / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "legacy-b0-id"}),
        encoding="utf-8",
    )

    # Active process running with legacy path in cmdline
    _make_proc(
        proc_root,
        pid=777,
        cmdline_args=["python3", "-m", "conscio.mcp.server", "--storage", str(legacy_space)],
    )

    rc = relay_cli.main(["doctor", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "777" in out
    assert str(legacy_space) in out or "migracao adiada" in out or "migração adiada" in out


def test_refused_markers_reported(tmp_path, capsys):
    """Refused markers: lists space-refused.json with registered state, reason, and age."""
    proc_root = tmp_path / "fake_proc"
    proc_root.mkdir(parents=True, exist_ok=True)

    fake_home = tmp_path / "fakehome"
    plugin_dir = fake_home / ".claude" / "plugins" / "data" / "conscio-conscio"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    marker_file = plugin_dir / "space-refused.json"
    marker_file.write_text(
        json.dumps({
            "schema": 1,
            "state": "B5",
            "reason": "pointer target missing or inaccessible",
            "ts": 1700000000.0,
        }),
        encoding="utf-8",
    )

    rc = relay_cli.main(["doctor", "--proc-root", str(proc_root)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "space-refused.json" in out or "recusa" in out
    assert "B5" in out
    assert "pointer target missing or inaccessible" in out
