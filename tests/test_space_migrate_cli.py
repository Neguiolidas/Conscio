from __future__ import annotations

import errno
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from conscio import cli


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_base = fake_home / ".conscio"
    fake_base.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CONSCIO_BASE", str(fake_base))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("ZCODE_PLUGIN_DATA", raising=False)
    host_keys = (
        "CLAUDECODE", "CLAUDE_CODE", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA",
        "ZCODE_PLUGIN_DATA", "ZCODE_PLUGIN_ID", "ZCODE_APP_VERSION", "ZCODE_PROJECT_DIR",
        "CHROME_DEVTOOLS_MCP_JS", "AGY_BROWSER_ACTIVE_PORT_FILE", "AGY_BROWSER_WS_URL",
        "ANTIGRAVITY_AGENT", "ANTIGRAVITY_AGENTAPI_EXE",
        "HERMES_HOME", "HERMES_SESSION_ID", "HERMES_AGENT",
        "OPENCODE_CONFIG_DIR", "OPENCODE_SERVER", "OPENCODE_PROJECT",
    )
    for k in host_keys:
        monkeypatch.delenv(k, raising=False)
    for k in list(os.environ.keys()):
        if k.startswith("CONSCIO_") and k != "CONSCIO_BASE":
            monkeypatch.delenv(k, raising=False)


def _setup_plugin_space(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "instance.json").write_text(
        json.dumps({
            "schema": 1,
            "instance_id": "orig-id-1234",
            "label": "claude-code",
            "created_ts": 1000.0,
        }),
        encoding="utf-8",
    )
    (storage / "obs.db").write_text("dummy-obs-db-data", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_dir))
    return plugin_dir, storage


def test_migrate_refused_lists_pids(tmp_path, monkeypatch, capsys):
    _plugin_dir, storage = _setup_plugin_space(tmp_path, monkeypatch)

    fake_proc = tmp_path / "fake_proc"
    proc_pid = fake_proc / "9999"
    proc_pid.mkdir(parents=True, exist_ok=True)
    cmdline = f"python3\x00server.py\x00--storage\x00{storage}\x00".encode()
    (proc_pid / "cmdline").write_bytes(cmdline)

    ret = cli.main(["space", "migrate", "--proc-root", str(fake_proc), "--quiet-minutes", "0"])
    assert ret == 2

    captured = capsys.readouterr()
    assert "migration deferred: 1 active process(es) on the legacy path:" in captured.err
    assert "PID 9999" in captured.err
    assert "systemctl --user stop" in captured.err

    # Contents must NOT have moved
    assert (storage / "instance.json").exists()
    # Migration lock must NOT remain
    base = Path(os.environ["CONSCIO_BASE"])
    assert not (base / "instances" / ".migrating-claude-code").exists()


def test_backup_two_generations(tmp_path, monkeypatch):
    _plugin_dir, _storage = _setup_plugin_space(tmp_path, monkeypatch)
    base = Path(os.environ["CONSCIO_BASE"])
    backups_dir = base / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    # Pre-existing backups
    (backups_dir / "pre-migrate-1000").mkdir()
    (backups_dir / "pre-migrate-1000" / "test1.txt").write_text("1000")
    (backups_dir / "pre-migrate-2000").mkdir()
    (backups_dir / "pre-migrate-2000" / "test2.txt").write_text("2000")

    empty_proc = tmp_path / "empty_proc"
    empty_proc.mkdir(parents=True, exist_ok=True)

    ret = cli.main(["space", "migrate", "--proc-root", str(empty_proc), "--quiet-minutes", "0"])
    assert ret == 0

    retained = sorted([p.name for p in backups_dir.iterdir() if p.name.startswith("pre-migrate-")])
    assert len(retained) == 2
    assert "pre-migrate-1000" not in retained
    assert "pre-migrate-2000" in retained


def test_migrate_moves_content_not_the_folder(tmp_path, monkeypatch, capsys):
    plugin_dir, storage = _setup_plugin_space(tmp_path, monkeypatch)
    subfolder = storage / "subdir"
    subfolder.mkdir(parents=True, exist_ok=True)
    (subfolder / "file.txt").write_text("sub-content", encoding="utf-8")

    # Put a space-refused marker in plugin_dir to ensure it gets cleared
    refused_marker = plugin_dir / "space-refused.json"
    refused_marker.write_text(json.dumps({"schema": 1, "state": "B3"}), encoding="utf-8")

    empty_proc = tmp_path / "empty_proc"
    empty_proc.mkdir(parents=True, exist_ok=True)

    ret = cli.main(["space", "migrate", "--proc-root", str(empty_proc), "--quiet-minutes", "0"])
    assert ret == 0

    # 1. space/ folder must still exist and be completely empty
    assert storage.exists()
    assert storage.is_dir()
    assert list(storage.iterdir()) == []

    # 2. durable target must contain all moved contents
    base = Path(os.environ["CONSCIO_BASE"])
    durable_dir = base / "instances" / "claude-code"
    assert durable_dir.exists()
    assert (durable_dir / "instance.json").exists()
    assert (durable_dir / "obs.db").read_text(encoding="utf-8") == "dummy-obs-db-data"
    assert (durable_dir / "subdir" / "file.txt").read_text(encoding="utf-8") == "sub-content"

    # 3. tombstone migrated-from.json must exist in durable target
    tombstone = durable_dir / "migrated-from.json"
    assert tombstone.exists()
    tomb_data = json.loads(tombstone.read_text(encoding="utf-8"))
    assert tomb_data["schema"] == 1
    assert tomb_data["origin"] == str(storage.resolve())
    assert tomb_data["slug"] == "claude-code"

    # 4. pointer space-pointer.json must exist in plugin_dir
    pointer = plugin_dir / "space-pointer.json"
    assert pointer.exists()
    ptr_data = json.loads(pointer.read_text(encoding="utf-8"))
    assert ptr_data["schema"] == 1
    assert ptr_data["target"] == str(durable_dir)
    assert ptr_data["slug"] == "claude-code"

    # 5. space-refused.json must be removed
    assert not refused_marker.exists()

    # 6. lock must be removed
    lock_path = base / "instances" / ".migrating-claude-code"
    assert not lock_path.exists()

    # 7. systemd reactor unit must be printed on stdout
    captured = capsys.readouterr()
    assert "[Unit]" in captured.out
    assert "ExecStart=" in captured.out
    assert str(durable_dir / "liaison.db") in captured.out


def test_migrate_exit_3_slug_undetermined(tmp_path, monkeypatch, capsys):
    # No plugin env vars, no host identity derivation possible
    empty_proc = tmp_path / "empty_proc"
    empty_proc.mkdir(parents=True, exist_ok=True)

    ret = cli.main(["space", "migrate", "--proc-root", str(empty_proc)])
    assert ret == 3

    captured = capsys.readouterr()
    assert "cannot determine space slug" in captured.err.lower() or "slug" in captured.err.lower()


def test_migrate_quiet_minutes_default_10(tmp_path, monkeypatch, capsys):
    _plugin_dir, storage = _setup_plugin_space(tmp_path, monkeypatch)
    empty_proc = tmp_path / "empty_proc"
    empty_proc.mkdir(parents=True, exist_ok=True)

    # File modified 60 seconds ago
    recent_file = storage / "recent.txt"
    recent_file.write_text("just-modified", encoding="utf-8")
    now = time.time()
    os.utime(recent_file, (now - 60, now - 60))

    # Default quiet_minutes is 10 (60s < 600s -> refused)
    ret = cli.main(["space", "migrate", "--proc-root", str(empty_proc)])
    assert ret == 2
    captured = capsys.readouterr()
    assert "quiet" in captured.err.lower() or "modified" in captured.err.lower()

    # Content must NOT have moved
    assert (storage / "instance.json").exists()

    # Passing --quiet-minutes 0 allows it to proceed
    ret2 = cli.main(["space", "migrate", "--proc-root", str(empty_proc), "--quiet-minutes", "0"])
    assert ret2 == 0
    assert not (storage / "instance.json").exists()


def test_migrate_cross_fs_exdev(tmp_path, monkeypatch):
    _plugin_dir, storage = _setup_plugin_space(tmp_path, monkeypatch)
    empty_proc = tmp_path / "empty_proc"
    empty_proc.mkdir(parents=True, exist_ok=True)

    def fake_rename(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    with patch("os.rename", side_effect=fake_rename):
        ret = cli.main(["space", "migrate", "--proc-root", str(empty_proc), "--quiet-minutes", "0"])
        assert ret == 0

    assert storage.exists()
    assert list(storage.iterdir()) == []
    base = Path(os.environ["CONSCIO_BASE"])
    durable_dir = base / "instances" / "claude-code"
    assert (durable_dir / "instance.json").exists()
    assert (durable_dir / "obs.db").read_text(encoding="utf-8") == "dummy-obs-db-data"
