"""Emit + verify a host's MCP launch config. Per-host binding lives here:
--storage <space> + env CONSCIO_VAULT_DIR=<space>/keys. Backs up before write
(R5) and reads back to confirm (never fails silently). Prunes old backups to
the most recent MAX_BACKUPS per config file (Hermet plan-gate ressalva)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from . import spaces

_FLAG_ARG = {"act": "--enable-act", "awake": "--awake",
             "relay": "--enable-relay", "initiate": "--initiate"}
MAX_BACKUPS = 30


class HostConfigError(RuntimeError):
    pass


def mcp_server_entry(slug: str, *, flags: dict, model: "str | None") -> dict:
    args = ["--storage", str(spaces.space_dir(slug))]
    if model:
        args += ["--model", model]
    for key, on in flags.items():
        if on and key in _FLAG_ARG:
            args.append(_FLAG_ARG[key])
    return {"command": "conscio-mcp", "args": args,
            "env": {"CONSCIO_VAULT_DIR": str(spaces.vault_dir(slug))}}


def _prune_backups(path: Path) -> None:
    baks = sorted(path.parent.glob(path.name + ".bak.*"))
    for old in baks[:-MAX_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def backup_then_write_json(path: Path, *, mutate: Callable[[dict], None],
                           verify: Callable[[dict], bool], ts: str) -> "Path | None":
    path = Path(path)
    backup = None
    obj: dict = {}
    if path.exists():
        backup = path.with_name(path.name + f".bak.{ts}")
        shutil.copy2(path, backup)
        try:
            obj = json.loads(path.read_text() or "{}")
            if not isinstance(obj, dict):
                obj = {}
        except (OSError, ValueError):
            obj = {}                       # corrupt: backup preserves the original
    mutate(obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True))
    tmp.replace(path)
    try:
        back = json.loads(path.read_text() or "{}")
    except (OSError, ValueError) as exc:
        raise HostConfigError(f"wrote {path} but it is unreadable: {exc}") from exc
    if not verify(back):
        raise HostConfigError(f"wrote {path} but the conscio entry is missing "
                              f"on read-back — config NOT applied")
    if backup is not None:
        _prune_backups(path)
    return backup


def write_claude_code(slug: str, *, flags: dict, model: "str | None",
                      config_path: Path, ts: str) -> "Path | None":
    def mutate(o: dict) -> None:
        o.setdefault("mcpServers", {})["conscio"] = mcp_server_entry(
            slug, flags=flags, model=model)
    return backup_then_write_json(
        config_path, mutate=mutate,
        verify=lambda o: "conscio" in o.get("mcpServers", {}), ts=ts)


def generic_snippet(slug: str, *, flags: dict, model: "str | None") -> str:
    return json.dumps(
        {"mcpServers": {"conscio": mcp_server_entry(slug, flags=flags,
                                                    model=model)}},
        indent=2, sort_keys=True)
