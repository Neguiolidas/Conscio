"""Install the Claude Code bundle into ~/.claude/, bound to a host space.
Idempotent; backs up edited JSON (corrupt files are backed up then rewritten
fresh). Reuses installer.hostcfg for the MCP entry + backup/verify."""
from __future__ import annotations

import json
import os
import shlex
import shutil
from pathlib import Path

from ...installer import hostcfg, spaces

_HOOK_NAME = "conscio_awareness.py"
_CAPTURE_HOOK = "conscio_deepminer.py"
# event name in settings.json -> argv token the hook dispatches on. The harness
# sends no event name on stdin, so it has to travel in the command line.
_CAPTURE_EVENTS = {
    "SessionStart": "session-start",
    "PostToolUse": "post-tool-use",
    "PostToolUseFailure": "post-tool-use-failure",
    "PreCompact": "pre-compact",
    "PostCompact": "post-compact",
}


def assets_root() -> Path:
    return Path(__file__).parent / "assets"


def _claude_dir(override) -> Path:
    if override is not None:
        return Path(override)
    return Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


def claude_json_path(override=None) -> Path:
    """Public: the wizard's --repair path reads existing flags from here."""
    if override is not None:
        return Path(override)
    return Path(os.environ.get("CLAUDE_JSON", str(Path.home() / ".claude.json")))


def _copy_tree(src: Path, dst: Path) -> int:
    # recursive: nested asset dirs (e.g. a skill's references/) must ship too
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            target = dst / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            n += 1
    return n


def materialize(slug: str, *, flags: dict, model, ts: str, io=None,
                claude_dir: Path | None = None,
                claude_json: Path | None = None) -> dict:
    cdir = _claude_dir(claude_dir)
    cjson = claude_json_path(claude_json)
    a = assets_root()

    n_cmds = _copy_tree(a / "commands", cdir / "commands" / "conscio")
    _copy_tree(a / "skills" / "conscio", cdir / "skills" / "conscio")
    hook_dst = cdir / "hooks" / _HOOK_NAME
    hook_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a / "hooks" / _HOOK_NAME, hook_dst)

    capture_dst = cdir / "hooks" / _CAPTURE_HOOK
    shutil.copy2(a / "hooks" / _CAPTURE_HOOK, capture_dst)
    # The hook loads obsstore by absolute path so it never imports the conscio
    # package (~0.28s) on a path that runs once per tool call.
    capture_dst.with_suffix(".json").write_text(json.dumps({
        "obsstore": str(Path(__file__).resolve().parents[2] / "obsstore.py"),
        "storage": str(spaces.space_dir(slug)),
    }, indent=2), encoding="utf-8")

    backups = []
    # 1) MCP registration (reuse hostcfg entry + backup/verify)
    def mut_mcp(o: dict) -> None:
        hostcfg.upsert_conscio_entry(o, slug, flags=flags, model=model)
    b = hostcfg.backup_then_write_json(
        cjson, mutate=mut_mcp,
        verify=lambda o: "conscio" in o.get("mcpServers", {}), ts=ts)
    if b:
        backups.append(str(b))

    # 2) SessionStart hook registration (idempotent: replace any prior conscio
    #    entry, then append fresh)
    settings = cdir / "settings.json"
    cmd = f"python3 {shlex.quote(str(hook_dst))}"       # path may carry spaces

    def mut_hook(o: dict) -> None:
        hooks = o.setdefault("hooks", {})
        starts = hooks.setdefault("SessionStart", [])
        kept = [grp for grp in starts if _HOOK_NAME not in json.dumps(grp)]
        kept.append({"hooks": [{"type": "command", "command": cmd}]})
        hooks["SessionStart"] = kept
        # Each hook is filtered and re-appended on its own so re-running the
        # installer never drops a sibling that shares the event.
        for event, arg in _CAPTURE_EVENTS.items():
            ccmd = f"python3 {shlex.quote(str(capture_dst))} {arg}"
            groups = [g for g in hooks.get(event, [])
                      if _CAPTURE_HOOK not in json.dumps(g)]
            groups.append({"hooks": [{"type": "command", "command": ccmd}]})
            hooks[event] = groups
    b2 = hostcfg.backup_then_write_json(
        settings, mutate=mut_hook,
        verify=lambda o: _HOOK_NAME in json.dumps(o.get("hooks", {})) and all(
            _CAPTURE_HOOK in json.dumps(o.get("hooks", {}).get(e, []))
            for e in _CAPTURE_EVENTS), ts=ts)
    if b2:
        backups.append(str(b2))

    summary = {"commands": n_cmds, "skill": True, "hook": True,
               "capture_hook": True, "mcp": "conscio", "backups": backups}
    if io is not None:
        io.echo(f"materialized Claude Code bundle: {n_cmds} commands, skill, "
                f"hook, MCP entry (storage bound to space {slug}).")
    return summary
