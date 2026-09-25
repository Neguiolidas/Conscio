from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from conscio.installer.durable import (
    plugin_data_dir,
    plugin_pointer_path,
    plugin_refused_marker_path,
    remove_refused_marker,
    resolve_space,
    write_refused_marker,
)
from conscio.installer.spaces import INSTANCES_ROOT
from conscio.mcp import server
from conscio.obsstore import resolve_hook_storage

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
    for k in (
        "CHROME_DEVTOOLS_MCP_JS",
        "AGY_BROWSER_ACTIVE_PORT_FILE",
        "AGY_BROWSER_WS_URL",
        "ANTIGRAVITY_AGENT",
        "ANTIGRAVITY_AGENTAPI_EXE",
        "HERMES_HOME",
        "HERMES_SESSION_ID",
        "HERMES_AGENT",
    ):
        monkeypatch.delenv(k, raising=False)
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


def test_hook_explicit_storage_ignores_plugin_root_pointer_and_refusal(tmp_path, monkeypatch):
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    other_target = tmp_path / "other_target"
    other_target.mkdir(parents=True, exist_ok=True)

    # Poison plugin dir with foreign pointer and refusal marker
    (plugin_dir / "space-pointer.json").write_text(
        json.dumps({
            "schema": 1,
            "target": str(other_target),
            "runtime": "claude-code",
            "slug": "claude-code",
            "migrated_ts": 1000.0,
        }),
        encoding="utf-8",
    )
    (plugin_dir / "space-refused.json").write_text(
        json.dumps({"schema": 1, "state": "B5", "reason": "foreign refusal", "ts": 1000.0}),
        encoding="utf-8",
    )

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_dir))
    explicit_storage = fake_home / "meu-espaco"
    explicit_storage.mkdir(parents=True, exist_ok=True)

    # 1. resolve_hook_storage must return explicit_storage, not other_target or None
    hook_res = resolve_hook_storage(explicit_storage)
    assert hook_res == explicit_storage

    # 2. Server's resolve_space and hook must agree on the same target
    server_res = resolve_space(explicit_storage)
    assert server_res.target == explicit_storage
    assert hook_res == server_res.target

    # 3. End-to-end hook execution via subprocess writes to explicit_storage
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_dir)
    payload = {
        "session_id": "session-explicit",
        "tool": "Bash",
        "input": {"command": "echo test"},
        "output": "test",
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(DEEPMINER_HOOK),
            "post-tool-use",
            "--obsstore",
            str(OBSSTORE_HOOK),
            "--storage",
            str(explicit_storage),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    assert (explicit_storage / "obs.db").exists()


def test_plugin_data_dir_none_for_non_bound_storage(tmp_path):
    explicit_storage = tmp_path / "meu-espaco"
    explicit_storage.mkdir(parents=True, exist_ok=True)

    assert plugin_data_dir(explicit_storage) is None
    assert plugin_pointer_path(explicit_storage) is None
    assert plugin_refused_marker_path(explicit_storage) is None

    # write_refused_marker should return None and not write anything inside explicit_storage
    res = write_refused_marker(explicit_storage, "B5", "test reason")
    assert res is None
    assert not (explicit_storage / "space-refused.json").exists()


def test_remove_refused_marker_preserves_user_file_in_non_bound_storage(tmp_path):
    explicit_storage = tmp_path / "meu-espaco"
    explicit_storage.mkdir(parents=True, exist_ok=True)
    planted_file = explicit_storage / "space-refused.json"
    planted_file.write_text(json.dumps({"user": "important-data"}), encoding="utf-8")

    remove_refused_marker(explicit_storage)

    assert planted_file.exists()
    assert json.loads(planted_file.read_text(encoding="utf-8")) == {"user": "important-data"}


def test_server_pointer_repair_uses_resolver_slug_and_runtime(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    fake_home = tmp_path / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_base = fake_home / ".conscio"
    fake_base.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CONSCIO_BASE", str(fake_base))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_dir))
    monkeypatch.setenv("CONSCIO_MODEL", "test-model")

    with patch("conscio.mcp.server.ConsciousnessEngine") as mock_engine, \
         patch("conscio.mcp.server.serve") as mock_serve:
        inst = mock_engine.return_value
        durable_space = fake_base / "instances" / "claude-code"
        durable_space.mkdir(parents=True, exist_ok=True)
        inst.storage = str(durable_space)
        mock_serve.return_value = None

        ret = server.main(["--storage", str(storage), "--identity-runtime", "different-runtime"])
        assert ret == 0

        ptr_file = plugin_dir / "space-pointer.json"
        assert ptr_file.exists()
        ptr_data = json.loads(ptr_file.read_text(encoding="utf-8"))
        assert ptr_data.get("slug") == "claude-code"
        assert ptr_data.get("runtime") == "claude-code"



