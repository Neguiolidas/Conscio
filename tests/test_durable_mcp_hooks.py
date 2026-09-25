from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from conscio.installer.spaces import INSTANCES_ROOT
from conscio.mcp import server

HOOKS_DIR = (
    Path(__file__).resolve().parent.parent
    / "conscio"
    / "integrations"
    / "claude_code"
    / "assets"
    / "hooks"
)
DEEPMINER_HOOK = HOOKS_DIR / "conscio_deepminer.py"
OBSSTORE_HOOK = HOOKS_DIR / "conscio_obsstore.py"


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
    for k in list(os.environ.keys()):
        if k.startswith("CONSCIO_") and k != "CONSCIO_BASE":
            monkeypatch.delenv(k, raising=False)


def test_server_refusal_exits_2_without_minting(tmp_path, monkeypatch, capsys):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    missing_target = INSTANCES_ROOT() / "missing-target"
    pointer_file = plugin_dir / "space-pointer.json"
    pointer_file.write_text(
        json.dumps({
            "schema": 1,
            "target": str(missing_target),
            "runtime": "claude-code",
            "slug": "claude-code",
            "migrated_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_dir))

    ret = server.main(["--storage", str(storage)])
    assert ret == 2

    # Check that space-refused.json was written
    refused_marker = plugin_dir / "space-refused.json"
    assert refused_marker.exists()
    refused_data = json.loads(refused_marker.read_text(encoding="utf-8"))
    assert refused_data["schema"] == 1
    assert refused_data["state"] == "B5"
    assert "space pointer found but its target" in refused_data["reason"]
    assert isinstance(refused_data["ts"], float)

    # Check stderr
    captured = capsys.readouterr()
    assert "space pointer found but its target" in captured.err

    # Check nothing was minted under INSTANCES_ROOT()
    assert not missing_target.exists()


def test_server_clears_refused_marker_on_resolve(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    # Pre-existing refused marker
    refused_marker = plugin_dir / "space-refused.json"
    refused_marker.write_text(
        json.dumps({"schema": 1, "state": "B5", "reason": "old", "ts": 1000.0}),
        encoding="utf-8",
    )

    # Valid B1 target
    target_dir = INSTANCES_ROOT() / "claude-code"
    target_dir.mkdir(parents=True, exist_ok=True)
    pointer_file = plugin_dir / "space-pointer.json"
    pointer_file.write_text(
        json.dumps({
            "schema": 1,
            "target": str(target_dir),
            "runtime": "claude-code",
            "slug": "claude-code",
            "migrated_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_dir))

    # Mock ConsciousnessEngine to stop server before entering event loop
    with patch("conscio.mcp.server.ConsciousnessEngine") as mock_engine:
        mock_engine.side_effect = RuntimeError("stop-after-setup")
        with pytest.raises(RuntimeError, match="stop-after-setup"):
            server.main(["--storage", str(storage), "--model", "dummy"])

    assert not refused_marker.exists()


def test_hook_follows_pointer(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    target_dir = INSTANCES_ROOT() / "claude-code"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "target-id-1234", "label": "test", "created_ts": 1000.0}),
        encoding="utf-8",
    )

    pointer_file = plugin_dir / "space-pointer.json"
    pointer_file.write_text(
        json.dumps({
            "schema": 1,
            "target": str(target_dir),
            "runtime": "claude-code",
            "slug": "claude-code",
            "migrated_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_dir)

    payload = {
        "session_id": "session-1",
        "tool": "Bash",
        "input": {"command": "ls"},
        "output": "file1.txt\nfile2.txt",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(DEEPMINER_HOOK),
            "post-tool-use",
            "--obsstore",
            str(OBSSTORE_HOOK),
            "--storage",
            str(storage),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    # Must write to durable target
    assert (target_dir / "obs.db").exists()
    # Must not write to legacy storage
    assert not (storage / "obs.db").exists()


def test_hook_skips_when_refused_marker(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    refused_marker = plugin_dir / "space-refused.json"
    refused_marker.write_text(
        json.dumps({"schema": 1, "state": "B3", "reason": "conflict", "ts": 1000.0}),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_dir)

    payload = {
        "session_id": "session-2",
        "tool": "Bash",
        "input": {"command": "pwd"},
        "output": "/home",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(DEEPMINER_HOOK),
            "post-tool-use",
            "--obsstore",
            str(OBSSTORE_HOOK),
            "--storage",
            str(storage),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    assert not (storage / "obs.db").exists()


def test_hook_skips_during_migration_lock(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    INSTANCES_ROOT().mkdir(parents=True, exist_ok=True)
    lock_file = INSTANCES_ROOT() / ".migrating-claude-code"
    lock_file.write_text(
        json.dumps({"schema": 1, "pid": 4321, "started_ts": 1000.0}),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_dir)

    payload = {
        "session_id": "session-3",
        "tool": "Bash",
        "input": {"command": "date"},
        "output": "Fri Sep 25",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(DEEPMINER_HOOK),
            "post-tool-use",
            "--obsstore",
            str(OBSSTORE_HOOK),
            "--storage",
            str(storage),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    assert not (storage / "obs.db").exists()


def test_hook_legacy_b0_writes_like_47(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "b0-legacy-id", "label": "test", "created_ts": 1000.0}),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_dir)

    payload = {
        "session_id": "session-4",
        "tool": "Bash",
        "input": {"command": "whoami"},
        "output": "ubuntu",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(DEEPMINER_HOOK),
            "post-tool-use",
            "--obsstore",
            str(OBSSTORE_HOOK),
            "--storage",
            str(storage),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    assert (storage / "obs.db").exists()


def test_server_without_storage_skips_s1_and_preserves_none(tmp_path, monkeypatch):
    cwd_path = tmp_path / "user_cwd"
    cwd_path.mkdir()
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_base = fake_home / ".conscio"
    fake_base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CONSCIO_BASE", str(fake_base))
    monkeypatch.setenv("CONSCIO_MODEL", "test-model")
    monkeypatch.chdir(cwd_path)

    with patch("conscio.mcp.server.ConsciousnessEngine") as mock_engine, \
         patch("conscio.mcp.server.serve") as mock_serve:
        inst = mock_engine.return_value
        default_storage = fake_base / "consciousness"
        default_storage.mkdir(parents=True, exist_ok=True)
        inst.storage = str(default_storage)
        mock_serve.return_value = None

        ret = server.main([])
        assert ret == 0
        call_kwargs = mock_engine.call_args.kwargs
        assert call_kwargs.get("storage_path") is None
        assert list(cwd_path.iterdir()) == []

