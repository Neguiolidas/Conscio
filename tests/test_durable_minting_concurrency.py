from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest

from conscio.installer.durable import (
    minting_lock,
    resolve_space,
    write_pointer_atomic,
)
from conscio.installer.spaces import ensure_space


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


def _boot_worker(slug: str, queue: multiprocessing.Queue, env_vars: dict[str, str]):
    for k, v in env_vars.items():
        os.environ[k] = v
    try:
        # Small jitter to ensure concurrent contention on the flock
        time.sleep(0.01)
        d, ident, created = ensure_space(slug)
        queue.put({"success": True, "instance_id": ident.instance_id, "created": created, "target": str(d)})
    except Exception as exc:
        queue.put({"success": False, "error": str(exc), "error_type": type(exc).__name__})


def test_b6_two_concurrent_boots_one_identity(tmp_path):
    slug = "concurrent-slug"
    env_vars = {
        "HOME": os.environ["HOME"],
        "CONSCIO_BASE": os.environ["CONSCIO_BASE"],
    }
    queue: multiprocessing.Queue = multiprocessing.Queue()

    p1 = multiprocessing.Process(target=_boot_worker, args=(slug, queue, env_vars))
    p2 = multiprocessing.Process(target=_boot_worker, args=(slug, queue, env_vars))

    p1.start()
    p2.start()

    p1.join(timeout=10)
    p2.join(timeout=10)

    assert p1.exitcode == 0, f"Process 1 failed with exitcode {p1.exitcode}"
    assert p2.exitcode == 0, f"Process 2 failed with exitcode {p2.exitcode}"

    res1 = queue.get(timeout=2)
    res2 = queue.get(timeout=2)

    assert res1["success"] is True, f"Worker 1 failed: {res1}"
    assert res2["success"] is True, f"Worker 2 failed: {res2}"

    assert res1["instance_id"] == res2["instance_id"]
    # Exactly one process created it, the other adopted
    assert {res1["created"], res2["created"]} == {True, False}


def test_b6_writes_pointer_and_hooks_write_first_boot(tmp_path):
    plugin_dir = tmp_path / "plugin"
    storage = plugin_dir / "space"
    env = {"CLAUDE_PLUGIN_DATA": str(plugin_dir)}

    res = resolve_space(storage, env=env)
    assert res.kind == "B6"
    assert res.repair_pointer is True
    assert res.target is not None

    slug = "claude-code"
    pointer_path = plugin_dir / "space-pointer.json"

    # First boot minting under lock
    with minting_lock(slug):
        durable_dir, _ident, _created = ensure_space(slug)
        write_pointer_atomic(
            pointer_path=pointer_path,
            target=durable_dir,
            runtime="claude-code",
            slug=slug,
        )

    assert pointer_path.exists()
    pointer_data = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer_data["schema"] == 1
    assert pointer_data["target"] == str(durable_dir)
    assert pointer_data["runtime"] == "claude-code"
    assert pointer_data["slug"] == slug
    assert isinstance(pointer_data["migrated_ts"], float)

    # Hook reads pointer and writes observation directly to durable store
    # Simulate hook following pointer
    hook_pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    effective_storage = Path(hook_pointer["target"])
    assert effective_storage == durable_dir
    assert (effective_storage / "instance.json").exists()


def test_minting_lock_timeout_raises_spec_message(tmp_path):
    slug = "timeout-slug"
    with minting_lock(slug, timeout=1.0), pytest.raises(TimeoutError) as exc_info:
        # We force a separate process to avoid thread re-entrancy
        ctx = multiprocessing.get_context("fork")
        q = ctx.Queue()

        def _try_lock(q_out, base_dir, s):
            from conscio.installer.spaces import _HELD_MINTING_LOCKS
            _HELD_MINTING_LOCKS.clear()
            os.environ["CONSCIO_BASE"] = base_dir
            try:
                with minting_lock(s, timeout=0.2):
                    q_out.put("acquired")
            except Exception as exc:
                q_out.put(exc)

        p = ctx.Process(target=_try_lock, args=(q, os.environ["CONSCIO_BASE"], slug))
        p.start()
        p.join(timeout=3)
        res = q.get(timeout=2)
        assert isinstance(res, TimeoutError)
        raise res

    expected_msg = (
        f"space minting in progress for {slug} (lock acquisition timed out). "
        "Refusing to proceed without exclusive minting lock. "
        "Re-run when current minting finishes."
    )
    assert str(exc_info.value) == expected_msg
