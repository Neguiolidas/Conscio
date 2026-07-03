"""Emit + verify a host's MCP launch config. Per-host binding lives here:
--storage <space> + env CONSCIO_VAULT_DIR=<space>/keys. Backs up before write
(R5) and reads back to confirm (never fails silently). Prunes old backups to
the most recent MAX_BACKUPS per config file (Hermet plan-gate ressalva)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from ..guards import safe_read_json
from . import spaces

# conscio-mcp launch flags only. "initiate" is a DAEMON flag (conscio-daemon
# --initiate); conscio-mcp rejects it, so it must never enter the MCP entry.
# "awake" stays even though the wizard never offers it: conscio-mcp accepts
# it and hand-tuned/pre-Reach entries carrying it must survive a --repair.
_FLAG_ARG = {"act": "--enable-act", "awake": "--awake",
             "relay": "--enable-relay", "hermes": "--enable-hermes-review"}
# args older installers emitted into the MCP entry: recovered as consents on
# --repair, but NEVER re-emitted (mcp_server_entry only knows _FLAG_ARG)
_LEGACY_ARG_FLAG = {"--initiate": "initiate"}
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


def upsert_conscio_entry(o: dict, slug: str, *, flags: dict,
                         model: "str | None") -> None:
    """Replace the conscio MCP entry, preserving user-added env keys on the
    old entry (ours win on conflict). Args are fully owned by the flags."""
    entry = mcp_server_entry(slug, flags=flags, model=model)
    servers = o.setdefault("mcpServers", {})
    if not isinstance(servers, dict):      # corrupt shape: rebuild (backed up)
        servers = o["mcpServers"] = {}
    old = servers.get("conscio")
    if isinstance(old, dict) and isinstance(old.get("env"), dict):
        entry["env"] = {**old["env"], **entry["env"]}
    servers["conscio"] = entry


def _entry_args(config_path: Path) -> list:
    """The existing conscio entry's args list; [] on missing/corrupt anything."""
    obj = safe_read_json(Path(config_path)) or {}
    servers = obj.get("mcpServers", {})
    entry = servers.get("conscio", {}) if isinstance(servers, dict) else {}
    args = entry.get("args", []) if isinstance(entry, dict) else []
    return args if isinstance(args, list) else []


def _arg_value(config_path: Path, flag: str) -> "str | None":
    args = _entry_args(config_path)
    for i, a in enumerate(args[:-1]):
        if a == flag:
            v = args[i + 1]
            return v if isinstance(v, str) and v else None
    return None


def existing_flags(config_path: Path) -> dict:
    """Inverse of mcp_server_entry: recover the consent flags from an existing
    conscio entry's args, so a --repair rewrite never silently strips a flag
    the user had granted. Includes _LEGACY_ARG_FLAG args (recovered as consent,
    never re-emitted). Missing/corrupt file or entry -> {}."""
    inv = {arg: key for key, arg in _FLAG_ARG.items()}
    inv.update(_LEGACY_ARG_FLAG)
    return {inv[a]: True for a in _entry_args(config_path) if a in inv}


def existing_model(config_path: Path) -> "str | None":
    """Recover --model from an existing conscio entry — a --repair without
    --model must not strip the configured model. None when absent/corrupt."""
    return _arg_value(config_path, "--model")


def existing_slug(config_path: Path) -> "str | None":
    """Recover the bound space slug from an existing entry's --storage arg —
    --repair must rebind the SAME space, never mint a new one."""
    v = _arg_value(config_path, "--storage")
    return Path(v).name if v else None


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
        n = 1
        while backup.exists():             # same-second rerun: never overwrite
            backup = path.with_name(path.name + f".bak.{ts}-{n}")
            n += 1
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
        upsert_conscio_entry(o, slug, flags=flags, model=model)
    return backup_then_write_json(
        config_path, mutate=mutate,
        verify=lambda o: "conscio" in o.get("mcpServers", {}), ts=ts)


def generic_snippet(slug: str, *, flags: dict, model: "str | None") -> str:
    return json.dumps(
        {"mcpServers": {"conscio": mcp_server_entry(slug, flags=flags,
                                                    model=model)}},
        indent=2, sort_keys=True)
