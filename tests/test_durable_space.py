from __future__ import annotations

import json
import multiprocessing
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

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
        slug="claude-code",
        runtime="claude-code",
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
        slug="claude-code",
        runtime="claude-code",
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
        slug="claude-code",
        runtime="claude-code",
    )


def test_b3_same_id_two_copies_asks(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug
    durable.mkdir(parents=True, exist_ok=True)

    shared_id = "aaaa1111-2222-3333-4444-555566667777"
    for path in (storage, durable):
        (path / "instance.json").write_text(
            json.dumps({"schema": 1, "instance_id": shared_id, "label": "test", "created_ts": 1000.0}),
            encoding="utf-8",
        )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B3"
    assert res.target is None
    assert res.repair_pointer is False
    assert (
        "two copies of the same identity for claude-code; they may have diverged (interrupted cross-fs copy or manual copy)"
        in res.reason
    )


def test_b3_conflicting_ids_asks(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug
    durable.mkdir(parents=True, exist_ok=True)

    plugin_id = "11112222-aaaa-bbbb-cccc-111122223333"
    durable_id = "88889999-xxxx-yyyy-zzzz-888899990000"
    (storage / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": plugin_id, "label": "test", "created_ts": 1000.0}),
        encoding="utf-8",
    )
    (durable / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": durable_id, "label": "test", "created_ts": 1000.0}),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B3"
    assert res.target is None
    assert res.repair_pointer is False
    assert (
        "identity conflict for claude-code: plugin copy (id 11112222) and durable copy (id 88889999) belong to different identities"
        in res.reason
    )


def test_b3_unreadable_instance_json(tmp_path):
    """Item 6: Corrupted instance.json in B3 reports (unreadable), not (unknown)."""
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    storage.mkdir(parents=True, exist_ok=True)
    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug
    durable.mkdir(parents=True, exist_ok=True)

    # Corrupted json in plugin copy -> unreadable
    (storage / "instance.json").write_text("NOT_VALID_JSON{", encoding="utf-8")
    (durable / "instance.json").write_text(
        json.dumps({"schema": 1, "instance_id": "88889999-xxxx-yyyy-zzzz", "label": "test"}),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B3"
    assert "plugin copy (id unreadable)" in res.reason
    assert "durable copy (id 88889999)" in res.reason

    # Valid JSON but missing instance_id field -> (unknown)
    (storage / "instance.json").write_text(json.dumps({"schema": 1}), encoding="utf-8")
    res2 = resolve_space(storage, env=env)
    assert res2.kind == "B3"
    assert "plugin copy (id unknown)" in res2.reason


def test_b4_evidence_without_space_asks(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug

    # Case 1: Tombstone migrated-from.json exists in durable dir, but instance.json does NOT exist
    durable.mkdir(parents=True, exist_ok=True)
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

    assert res.kind == "B4"
    assert res.target is None
    assert res.repair_pointer is False
    assert f"evidence of a previous identity for {slug} was found" in res.reason
    assert (
        "but no live space exists. Not minting silently — resolve with 'conscio space migrate' or delete the evidence explicitly."
        in res.reason
    )

    # Case 2: No tombstone, but card in relay peers directory exists
    (durable / "migrated-from.json").unlink()
    from conscio.liaison import directory
    peers_dir = directory.peers_dir()
    peers_dir.mkdir(parents=True, exist_ok=True)
    card_id = "test-peer-id-1234"
    (peers_dir / f"{card_id}.json").write_text(
        json.dumps({
            "instance_id": card_id,
            "runtime": "claude-code",
            "familia": "claude",
            "modelo": "claude-3-7-sonnet",
        }),
        encoding="utf-8",
    )

    res2 = resolve_space(storage, env=env)
    assert res2.kind == "B4"
    assert res2.target is None
    assert res2.repair_pointer is False
    assert f"evidence of a previous identity for {slug} was found" in res2.reason
    assert (
        "but no live space exists. Not minting silently — resolve with 'conscio space migrate' or delete the evidence explicitly."
        in res2.reason
    )


def test_b4_directory_peers_failure_refuses(tmp_path, monkeypatch):
    """Item 3: directory.peers exception causes B4 refusal instead of silent B6 minting."""
    from conscio.liaison import directory

    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    slug = "claude-code"
    durable = INSTANCES_ROOT() / slug

    def broken_peers():
        raise PermissionError("Access denied reading relay peers")

    monkeypatch.setattr(directory, "peers", broken_peers)

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "B4"
    assert res.target is None
    assert res.repair_pointer is False
    expected_reason = (
        f"could not check the relay directory for a previous identity of {slug} "
        "(PermissionError: Access denied reading relay peers); not minting silently — "
        "fix access to ~/.conscio/relay/peers or run 'conscio space migrate'."
    )
    assert res.reason == expected_reason
    # Nothing was minted or written
    assert not durable.exists()


def test_b4_remote_peer_card_is_skipped(tmp_path):
    """Item 5: Remote peer card (with url and no spool) is skipped in B4, falling through to B6."""
    from conscio.liaison import directory

    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"

    peers_dir = directory.peers_dir()
    peers_dir.mkdir(parents=True, exist_ok=True)
    card_id = "remote-peer-card-1"
    (peers_dir / f"{card_id}.json").write_text(
        json.dumps({
            "instance_id": card_id,
            "runtime": "claude-code",
            "familia": "claude",
            "url": "https://remote-host:8443",
            "spool": None,
        }),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}

    # Remote card with same runtime + nothing else -> B6 (mints)
    res_remote = resolve_space(storage, env=env)
    assert res_remote.kind == "B6"
    assert res_remote.repair_pointer is True

    # Same card as local (remove url or add spool) -> B4
    (peers_dir / f"{card_id}.json").write_text(
        json.dumps({
            "instance_id": card_id,
            "runtime": "claude-code",
            "familia": "claude",
            "spool": "/path/to/spool",
        }),
        encoding="utf-8",
    )
    res_local = resolve_space(storage, env=env)
    assert res_local.kind == "B4"
    assert res_local.target is None


def test_boot_during_lock_refuses(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    slug = "claude-code"
    lock_file = INSTANCES_ROOT() / f".migrating-{slug}"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(
        json.dumps({"schema": 1, "pid": 1454710, "started_ts": 1000.0}),
        encoding="utf-8",
    )

    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}
    res = resolve_space(storage, env=env)

    assert res.kind == "lock"
    assert res.target is None
    assert res.repair_pointer is False
    expected_msg = (
        "space migration in progress for claude-code (pid 1454710). "
        "Refusing to start: neither minting nor using the legacy path. "
        "Re-run when the migration finishes."
    )
    assert res.reason == expected_msg


def _lock_concurrency_worker(slug, env_vars, event_locked, event_step1_done, event_unlocked, queue):
    for k, v in env_vars.items():
        os.environ[k] = v
    plugin_dir = Path(env_vars["CLAUDE_PLUGIN_DATA"])
    storage = plugin_dir / "space"
    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}

    # Wait until migration lock is established
    event_locked.wait(timeout=5)
    res_locked = resolve_space(storage, env=env)
    queue.put({"phase": "locked", "kind": res_locked.kind, "target": str(res_locked.target)})

    # Signal step 1 completed
    event_step1_done.set()

    # Wait until migration lock is released
    event_unlocked.wait(timeout=5)
    res_unlocked = resolve_space(storage, env=env)
    queue.put({"phase": "unlocked", "kind": res_unlocked.kind, "target": str(res_unlocked.target)})


def test_boot_during_lock_concurrency_with_event(tmp_path):
    # remover os eventos transforma o teste em falso-verde
    plugin_dir = tmp_path / "plugin"
    slug = "claude-code"
    lock_file = INSTANCES_ROOT() / f".migrating-{slug}"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    ctx = multiprocessing.get_context("fork")
    event_locked = ctx.Event()
    event_step1_done = ctx.Event()
    event_unlocked = ctx.Event()
    queue = ctx.Queue()

    env_vars = {
        "HOME": os.environ["HOME"],
        "CONSCIO_BASE": os.environ["CONSCIO_BASE"],
        "CLAUDE_PLUGIN_DATA": str(plugin_dir),
    }

    p = ctx.Process(
        target=_lock_concurrency_worker,
        args=(slug, env_vars, event_locked, event_step1_done, event_unlocked, queue),
    )
    p.start()

    try:
        # Phase 1: Create lock file and signal worker
        lock_file.write_text(
            json.dumps({"schema": 1, "pid": 9999, "started_ts": 1000.0}),
            encoding="utf-8",
        )
        event_locked.set()

        # Wait for worker to test while locked
        assert event_step1_done.wait(timeout=5)
        item1 = queue.get(timeout=2)
        assert item1["phase"] == "locked"
        assert item1["kind"] == "lock"
        assert item1["target"] == "None"

        # Phase 2: Remove lock file and signal worker
        lock_file.unlink()
        event_unlocked.set()

        p.join(timeout=5)
        assert p.exitcode == 0

        item2 = queue.get(timeout=2)
        assert item2["phase"] == "unlocked"
        # When lock is removed, boot no longer refuses with "lock"
        assert item2["kind"] != "lock"
        assert item2["kind"] == "B6"
    finally:
        if p.is_alive():
            p.terminate()
            p.join(timeout=2)
