"""Install the Claude Code bundle into ~/.claude/, bound to a host space.
Idempotent; backs up edited JSON (corrupt files are backed up then rewritten
fresh). Reuses installer.hostcfg for the MCP entry + backup/verify."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ...installer import hostcfg

_HOOK_NAME = "conscio_awareness.py"


def assets_root() -> Path:
    return Path(__file__).parent / "assets"


def _claude_dir(override) -> Path:
    if override is not None:
        return Path(override)
    return Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


def _claude_json(override) -> Path:
    if override is not None:
        return Path(override)
    return Path(os.environ.get("CLAUDE_JSON", str(Path.home() / ".claude.json")))


def _copy_tree(src: Path, dst: Path) -> int:
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.glob("*")):
        if p.is_file():
            shutil.copy2(p, dst / p.name)
            n += 1
    return n


def materialize(slug: str, *, flags: dict, model, ts: str, io=None,
                claude_dir: "Path | None" = None,
                claude_json: "Path | None" = None) -> dict:
    cdir = _claude_dir(claude_dir)
    cjson = _claude_json(claude_json)
    a = assets_root()

    n_cmds = _copy_tree(a / "commands", cdir / "commands" / "conscio")
    _copy_tree(a / "skills" / "conscio", cdir / "skills" / "conscio")
    hook_dst = cdir / "hooks" / _HOOK_NAME
    hook_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a / "hooks" / _HOOK_NAME, hook_dst)

    backups = []
    # 1) MCP registration (reuse hostcfg entry + backup/verify)
    def mut_mcp(o: dict) -> None:
        o.setdefault("mcpServers", {})["conscio"] = hostcfg.mcp_server_entry(
            slug, flags=flags, model=model)
    b = hostcfg.backup_then_write_json(
        cjson, mutate=mut_mcp,
        verify=lambda o: "conscio" in o.get("mcpServers", {}), ts=ts)
    if b:
        backups.append(str(b))

    # 2) SessionStart hook registration (idempotent: replace any prior conscio
    #    entry, then append fresh)
    settings = cdir / "settings.json"
    cmd = f"python3 {hook_dst}"

    def mut_hook(o: dict) -> None:
        hooks = o.setdefault("hooks", {})
        starts = hooks.setdefault("SessionStart", [])
        kept = [grp for grp in starts if _HOOK_NAME not in json.dumps(grp)]
        kept.append({"hooks": [{"type": "command", "command": cmd}]})
        hooks["SessionStart"] = kept
    b2 = hostcfg.backup_then_write_json(
        settings, mutate=mut_hook,
        verify=lambda o: _HOOK_NAME in json.dumps(o.get("hooks", {})), ts=ts)
    if b2:
        backups.append(str(b2))

    summary = {"commands": n_cmds, "skill": True, "hook": True,
               "mcp": "conscio", "backups": backups}
    if io is not None:
        io.echo(f"materialized Claude Code bundle: {n_cmds} commands, skill, "
                f"hook, MCP entry (storage bound to space {slug}).")
    return summary
