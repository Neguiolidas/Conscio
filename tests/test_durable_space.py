from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError

import pytest

from conscio.installer.durable import (
    SpaceResolution,
    resolve_space,
    write_pointer_atomic,
)
from conscio.installer.spaces import INSTANCES_ROOT


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


def test_resolve_space_is_pure_no_side_effects(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}

    res = resolve_space(storage, env=env)

    assert isinstance(res, SpaceResolution)
    assert not plugin_dir.exists()
    assert not INSTANCES_ROOT().exists()

    with pytest.raises(FrozenInstanceError):
        res.kind = "MUTATED"  # type: ignore[misc]


def test_b6_fresh_mints(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}

    res = resolve_space(storage, env=env)

    expected_target = INSTANCES_ROOT() / "claude-code"
    assert res == SpaceResolution(
        kind="B6",
        target=expected_target,
        reason="",
        repair_pointer=True,
        announcement="",
    )


def test_b2_after_wipe_without_identity_adopts(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug
    durable.mkdir(parents=True, exist_ok=True)
    (durable / "instance.json").write_text(
        json.dumps({
            "schema": 1,
            "instance_id": "12345678-1111-2222-3333-444455556666",
            "label": "test",
            "created_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B2"
    assert res.target == durable
    assert res.repair_pointer is True
    assert res.reason == ""


def test_b2_announces_reactor_rearm(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug
    durable.mkdir(parents=True, exist_ok=True)
    (durable / "instance.json").write_text(
        json.dumps({
            "schema": 1,
            "instance_id": "87654321-1111-2222-3333-444455556666",
            "label": "test",
            "created_ts": 1000.0,
        }),
        encoding="utf-8",
    )
    (durable / "migrated-from.json").write_text(
        json.dumps({
            "schema": 1,
            "origin": str(storage),
            "runtime": "claude-code",
            "slug": slug,
            "migrated_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B2"
    assert res.target == durable
    assert "space adopted for claude-code (instance 87654321)" in res.announcement
    assert (
        "reactor unit for this agent points to legacy path — re-arm with: conscio relay service ..."
        in res.announcement
    )


def test_b2_reactor_warning_only_with_tombstone(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)

    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug
    durable.mkdir(parents=True, exist_ok=True)
    (durable / "instance.json").write_text(
        json.dumps({
            "schema": 1,
            "instance_id": "12345678-1111-2222-3333-444455556666",
            "label": "test",
            "created_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B2"
    assert res.target == durable
    assert res.announcement == ""


def test_b0_legacy_used_until_migrate(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "instance.json").write_text(
        json.dumps({
            "schema": 1,
            "instance_id": "abcdef12-1111-2222-3333-444455556666",
            "label": "test",
            "created_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B0"
    assert res.target == storage
    assert res.repair_pointer is False
    assert res.reason == ""


def test_b0_announces_migration(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "instance.json").write_text(
        json.dumps({
            "schema": 1,
            "instance_id": "abcdef12-1111-2222-3333-444455556666",
            "label": "test",
            "created_ts": 1000.0,
        }),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B0"
    assert res.announcement == "migration ready, run: conscio space migrate"


def test_b1_pointer_valid(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    target_dir = INSTANCES_ROOT() / "claude-code"
    target_dir.mkdir(parents=True, exist_ok=True)
    pointer_file = plugin_dir / "space-pointer.json"
    write_pointer_atomic(
        pointer_path=pointer_file,
        target=target_dir,
        runtime="claude-code",
        slug="claude-code",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res == SpaceResolution(
        kind="B1",
        target=target_dir,
        reason="",
        repair_pointer=False,
        announcement="",
    )


def test_b5_dangling_pointer_refuses(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    missing_target = INSTANCES_ROOT() / "missing-target"
    pointer_file = plugin_dir / "space-pointer.json"
    write_pointer_atomic(
        pointer_path=pointer_file,
        target=missing_target,
        runtime="claude-code",
        slug="claude-code",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    expected_reason = (
        f"space pointer found but its target {missing_target} is gone. "
        "Not minting over it — run 'conscio space migrate' to resolve, or restore the target."
    )
    assert res == SpaceResolution(
        kind="B5",
        target=None,
        reason=expected_reason,
        repair_pointer=False,
        announcement="",
    )
