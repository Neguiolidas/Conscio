"""Legacy Claude Code materialized install: find and retire it.

v2.11 through v4.9.1 shipped `conscio init --host claude-code` as a
materializer: it copied hooks, commands and skills into ~/.claude, registered
hook groups in settings.json and wrote an MCP entry into ~/.claude.json.
v4.9.2 made the plugin the only install path, but nothing removed those
copies — with the plugin installed every hook ran twice and two `conscio` MCP
servers were registered. This module finds what is left and moves it (never
deletes) into a timestamped backup, guarding the user's space identity.

Read-only until `remove_legacy` is explicitly called. `find_leftovers` never
parses or opens anything beyond the paths listed below.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .wizard import PromptIO

#: Exact legacy hook manifest (union over every tag that shipped
#: materialize.py). The awareness hook keeps its own literal copy of this
#: list; a contract test asserts the two stay equal.
HOOK_FILES = [
    "conscio_awareness.py",
    "conscio_deepminer.py",
    "conscio_deepminer.json",
    "conscio_obsstore.py",
    "conscio_wake.py",
    "conscio_honesty.py",
    "conscio_honesty_pkg/__init__.py",
]


def claude_dir(env: Mapping[str, str] | None = None) -> Path:
    """The claude dir, resolved exactly like v4.9.1 materialize.py."""
    env = dict(os.environ) if env is None else env
    override = env.get("CLAUDE_DIR")
    if override:
        return Path(override).expanduser()
    return Path(env.get("HOME", str(Path.home()))).expanduser() / ".claude"


def claude_json_path(env: Mapping[str, str] | None = None) -> Path:
    """The .claude.json path, resolved exactly like v4.9.1 materialize.py."""
    env = dict(os.environ) if env is None else env
    override = env.get("CLAUDE_JSON")
    if override:
        return Path(override).expanduser()
    return Path(env.get("HOME", str(Path.home()))).expanduser() / ".claude.json"


def find_leftovers(env: Mapping[str, str] | None = None) -> dict:
    """Read-only report of what the old materialized install left behind.

    Returns {"files": [...], "dirs": [...], "settings_groups": [...],
    "mcp_entry": {...} | None, "storage": Path | None}.
    """
    claude = claude_dir(env)
    files: list[Path] = []
    dirs: list[Path] = []
    for rel in HOOK_FILES:
        candidate = claude / "hooks" / rel
        if candidate.is_file():
            files.append(candidate)
    pkg_dir = claude / "hooks" / "conscio_honesty_pkg"
    if pkg_dir.is_dir():
        dirs.append(pkg_dir)
    pycache = claude / "hooks" / "__pycache__"
    if pycache.is_dir() and any(pycache.glob("conscio_*.pyc")):
        dirs.append(pycache)
    for rel in ("commands/conscio", "skills/conscio", "skills/conscio-relay"):
        candidate = claude / rel
        if candidate.is_dir():
            dirs.append(candidate)

    settings_groups: list[str] = []
    settings_path = claude / "settings.json"
    hook_abs = {str(claude / "hooks" / rel) for rel in HOOK_FILES}
    settings_data: dict | None = None
    try:
        settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        settings_data = None
    hooks_section = (settings_data or {}).get("hooks")
    if isinstance(hooks_section, dict):
        for group_name, matchers in hooks_section.items():
            if not isinstance(matchers, list):
                continue
            for matcher in matchers:
                if not isinstance(matcher, dict):
                    continue
                for hook in matcher.get("hooks", []) or []:
                    command = str(hook.get("command", ""))
                    if any(abs_path in command for abs_path in hook_abs):
                        settings_groups.append(group_name)
                        break
                else:
                    continue
                break

    mcp_entry: dict | None = None
    storage: Path | None = None
    json_path = claude_json_path(env)
    try:
        claude_json = json.loads(json_path.read_text(encoding="utf-8"))
        entry = claude_json.get("mcpServers", {}).get("conscio")
        args = entry.get("args", []) if isinstance(entry, dict) else []
        if isinstance(args, list) and "--storage" in args:
            mcp_entry = entry
            s_raw = args[args.index("--storage") + 1]
            storage = Path(s_raw).expanduser()
    except Exception:
        mcp_entry = None

    return {"files": files, "dirs": dirs, "settings_groups": settings_groups,
            "mcp_entry": mcp_entry, "storage": storage}


def _claude_code_env(env: dict) -> dict:
    """An env that derives the claude-code identity, for the guard.

    derive_host_identity's precedence (ZCode primary > Antigravity primary >
    Claude) means signals inherited from an enclosing ZCode session would
    steal the runtime, so they are stripped and the Claude Code native key
    is set instead — the CLI may run in a plain shell with CLAUDECODE unset.
    """
    _strip = ("ZCODE_PLUGIN_DATA", "ZCODE_PLUGIN_ID",
              "CHROME_DEVTOOLS_MCP_JS", "AGY_BROWSER_ACTIVE_PORT_FILE",
              "AGY_BROWSER_WS_URL")
    forced = {k: v for k, v in env.items() if k not in _strip}
    forced["CLAUDECODE"] = "1"
    return forced


def _plugin_space_resolution(env: dict) -> str | None:
    """The space the plugin (claude-code identity) resolves to, or None."""
    try:
        from .durable import known_plugin_data_dirs, resolve_space
    except Exception:
        return None
    forced = _claude_code_env(env)
    for root in known_plugin_data_dirs(env=forced, only_existing=True):
        # the MCP server writes the pointer at the plugin data root
        pointer = root / "space-pointer.json"
        if pointer.is_file():
            try:
                target_str = json.loads(
                    pointer.read_text(encoding="utf-8")).get("target", "")
                target = Path(str(target_str)).expanduser()
                if target_str and target.is_dir():
                    return str(target)
            except (OSError, ValueError):
                pass
        candidate = root / "space"
        if candidate.is_dir():
            res = resolve_space(candidate, env=forced)
            if res.target is not None:
                return str(res.target)
    return None


def _durable_default_space() -> Path:
    from . import spaces
    return spaces.space_dir("claude-code")


def _plugin_has_live_space(env: dict) -> bool:
    """True when a known plugin data dir holds a live space (instance.json in
    <root>/space) even though resolve_space refused to pick one (a B3
    conflict). Distinguishes "the plugin runs a distinct live space" (refuse)
    from "no plugin is installed" (proceed: the plugin will adopt the bound
    space via B2, so a cleanup cannot strand it)."""
    try:
        from .durable import known_plugin_data_dirs
    except Exception:
        return False
    forced = _claude_code_env(env)
    for root in known_plugin_data_dirs(env=forced, only_existing=True):
        if (root / "space" / "instance.json").is_file():
            return True
    return False


def _stop_hook_storage(env: Mapping[str, str]) -> Path | None:
    """The --storage the legacy Stop hook carries (v4.5.4+ wrote it into
    the hook command): the binding evidence when no MCP entry remains.
    None when no such command is present; unreadable/unparseable settings
    is no evidence."""
    claude = claude_dir(env)
    settings_path = claude / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return None
    for matchers in hooks.values():
        if not isinstance(matchers, list):
            continue
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            for h in matcher.get("hooks") or []:
                if not isinstance(h, dict):
                    continue
                command = str(h.get("command", ""))
                if "conscio_wake.py" not in command \
                        or "--storage" not in command:
                    continue
                parts = command.split()
                for i, part in enumerate(parts[:-1]):
                    if part == "--storage":
                        value = parts[i + 1].strip("'\"")
                        if value:
                            return Path(value).expanduser()
    return None


def _is_conscio_entry(hook_abs: set, hook: object) -> bool:
    """Whether a settings hook entry is ours: its command names a Conscio
    hook file under the claude dir."""
    if not isinstance(hook, dict):
        return False
    command = str(hook.get("command", ""))
    return any(abs_path in command for abs_path in hook_abs)


def _settings_has_conscio_entry(settings: object, hook_abs: set) -> bool:
    """The verify predicate: any hook entry still naming a Conscio hook
    file."""
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for matchers in hooks.values():
        if not isinstance(matchers, list):
            continue
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            for h in matcher.get("hooks") or []:
                if _is_conscio_entry(hook_abs, h):
                    return True
    return False


def _prune_conscio_entries(hooks_section: dict,
                           hook_abs: set) -> bool:
    """Prune in place at hook-entry granularity (F3/A2): drop only the
    entries whose command names a Conscio hook file. A group (matcher)
    drops only when it ends empty; an event key drops only when it ends
    empty — restoring the pre-install state. User entries in a group are
    kept as the same objects (byte-equal). Returns True when anything
    changed."""
    changed = False
    for event, matchers in list(hooks_section.items()):
        if not isinstance(matchers, list):
            continue
        event_touched = False
        new_matchers: list = []
        for matcher in matchers:
            if not isinstance(matcher, dict):
                new_matchers.append(matcher)
                continue
            entries = matcher.get("hooks")
            if not isinstance(entries, list):
                new_matchers.append(matcher)
                continue
            kept = [h for h in entries
                   if not _is_conscio_entry(hook_abs, h)]
            if len(kept) == len(entries):
                new_matchers.append(matcher)       # user's group: verbatim
            elif kept:
                pruned = dict(matcher)
                pruned["hooks"] = kept
                new_matchers.append(pruned)
                event_touched = True
            else:
                event_touched = True                # group ended empty: dropped
        if event_touched:
            changed = True
            if not new_matchers:
                hooks_section.pop(event, None)      # event key ended empty
            elif new_matchers != matchers:
                hooks_section[event] = new_matchers
    return changed


def remove_legacy(*, env: dict | None = None,
                  retire_previous_space: bool = False,
                  io: PromptIO) -> int:
    """Move every leftover into legacy-backup/<UTC ts>/ and clean the JSONs.

    Identity guard first: the cleanup proceeds only when the legacy space S
    (the --storage of the old MCP entry) is the one the plugin uses or will
    adopt — otherwise the user's memory would be silently orphaned.
    Moves, never deletes: files and dirs are renamed into a timestamped
    backup, and both JSON files are rewritten through
    hostcfg.backup_then_write_json (backup + verify). Idempotent: a second
    run finds nothing left and exits 0.
    """
    env = env if env is not None else dict(os.environ)
    report = find_leftovers(env)
    if not (report["files"] or report["dirs"] or report["settings_groups"]
            or report["mcp_entry"]):
        io.echo("nothing to remove")
        return 0

    storage = report["storage"]
    plugin_space = _plugin_space_resolution(env)
    if storage is not None and (storage / "instance.json").is_file():
        if plugin_space is not None and Path(plugin_space) != storage:
            if not retire_previous_space:
                io.echo(
                    f"the plugin resolves to a different live identity "
                    f"({plugin_space}); the legacy space {storage} would be "
                    "orphaned. start Claude Code once so the plugin adopts "
                    "the legacy space, or re-run with --retire-previous-space "
                    "to proceed: the legacy space stays on disk, untouched, "
                    "no longer run.")
                return 2
            io.echo(f"the legacy space {storage} stays on disk, untouched, "
                    "no longer run.")
        elif plugin_space is None:
            durable_default = _durable_default_space()
            if storage != durable_default:
                io.echo(
                    "no plugin is installed and the legacy space is a custom "
                    "label the plugin will not adopt. Install the plugin and "
                    "start Claude Code once so it adopts "
                    f"{storage}, then rerun this command.")
                return 2
    elif storage is None:
        # No MCP entry names the legacy space. F4: "stranded" means only the
        # space the legacy Claude install was bound to — here the Stop
        # hook's --storage (v4.5.4+ wrote it), else the default claude-code
        # slot. Other runtimes' live spaces (instances/zcode,
        # instances/hermes, ...) are never stranded by retiring Claude's
        # leftovers, so they are never counted here. The space is stranded
        # when it is live and the plugin does not run it — a different live
        # resolution, or NO live resolution at all (a B3 conflict belongs to
        # `conscio space migrate --keep`; the legacy files must not be
        # cleaned out from under it either).
        bound = _stop_hook_storage(env) or _durable_default_space()
        if not (bound / "instance.json").is_file():
            pass                                   # bound space not live: nothing to strand
        elif plugin_space is not None and Path(plugin_space) != bound:
            if not retire_previous_space:
                io.echo(
                    f"the legacy-bound space {bound} is live but the plugin "
                    f"resolves to a different live identity "
                    f"({plugin_space}); it would be stranded. start Claude "
                    "Code once so the plugin adopts the legacy space, or "
                    "re-run with --retire-previous-space to proceed anyway: "
                    "the space stays on disk, untouched, no longer run.")
                return 2
            io.echo(f"the legacy-bound space {bound} stays on disk, "
                    "untouched, no longer run.")
        elif plugin_space is None \
                and _plugin_has_live_space(env):
            # B3: the plugin runs a distinct live space, so the bound one
            # would be stranded. (A plain "no plugin" falls through: the
            # plugin will adopt the bound space via B2, so a cleanup cannot
            # strand it.)
            if not retire_previous_space:
                io.echo(
                    f"the legacy-bound space {bound} is live and the plugin "
                    "runs a distinct live space of its own; the bound one "
                    "would be stranded. start Claude Code once and resolve "
                    "the space conflict with 'conscio space migrate --keep "
                    "plugin' or '--keep durable' so the plugin adopts the "
                    "bound space, or re-run with --retire-previous-space to "
                    "proceed anyway: the space stays on disk, untouched, no "
                    "longer run.")
                return 2
            io.echo(f"the legacy-bound space {bound} stays on disk, "
                    "untouched, no longer run.")
    # else: the entry names a space that is not live — nothing to strand.

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    base = Path(env.get("CONSCIO_BASE",
                        str(home / ".conscio"))).expanduser()
    backup_root = base / "legacy-backup" / stamp
    claude_root = claude_dir(env)

    # F6 pre-flight: both JSON files must parse BEFORE anything moves.
    # Unparseable -> exit 2 with nothing touched, naming the file: moving
    # the files first and then failing on the JSON would leave a half
    # cleanup that a rerun cannot finish (the leftovers are gone but the
    # JSON is still broken).
    settings_path = claude_root / "settings.json"
    json_path = claude_json_path(env)
    settings_data: dict | None = None
    if settings_path.exists():
        try:
            parsed = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            io.echo(f"refusing to remove the legacy files: settings.json is "
                    f"unparseable ({exc}); fix or delete it, then rerun. "
                    "Nothing was moved.")
            return 2
        settings_data = parsed if isinstance(parsed, dict) else None
    claude_json_data: dict | None = None
    if json_path.exists():
        try:
            parsed = json.loads(json_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            io.echo(f"refusing to remove the legacy files: .claude.json is "
                    f"unparseable ({exc}); fix or delete it, then rerun. "
                    "Nothing was moved.")
            return 2
        claude_json_data = parsed if isinstance(parsed, dict) else None

    moved = 0
    try:
        # dirs first (their files travel inside), then standalone files —
        # skipping any file already moved with its parent dir.
        for path in report["dirs"]:
            rel = path.relative_to(claude_root.parent)
            dest = backup_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            moved += 1
        for path in report["files"]:
            if any(path.is_relative_to(d) for d in report["dirs"]):
                continue
            rel = path.relative_to(claude_root.parent)
            dest = backup_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            moved += 1

        # prune parent dirs the manifest leaves behind (only when empty)
        for rel in ("hooks", "commands", "skills"):
            candidate = claude_root / rel
            if candidate.is_dir() and not any(candidate.iterdir()):
                candidate.rmdir()

        # settings.json: prune at hook-ENTRY granularity (F3/A2) — drop
        # only the entries whose command names a Conscio hook file; a
        # group drops only when it ends empty, an event key only when it
        # ends empty. User entries survive verbatim, in their own group
        # and mixed with Conscio ones.
        hooks_section = (settings_data or {}).get("hooks")
        if isinstance(hooks_section, dict):
            hook_abs = {str(claude_root / "hooks" / rel) for rel in HOOK_FILES}
            if _prune_conscio_entries(hooks_section, hook_abs):
                from . import hostcfg
                ts = time.strftime("%Y%m%d-%H%M%S")
                hostcfg.backup_then_write_json(
                    settings_path,
                    mutate=lambda o: o.update(hooks=hooks_section),
                    verify=lambda o: not _settings_has_conscio_entry(
                        o, hook_abs),
                    ts=ts)

        # .claude.json: rewrite only when it actually has
        # mcpServers.conscio (F6) — otherwise it stays byte-identical,
        # no .bak side file.
        if claude_json_data is not None \
                and isinstance(claude_json_data.get("mcpServers"), dict) \
                and "conscio" in claude_json_data["mcpServers"]:
            from . import hostcfg
            claude_json_data["mcpServers"].pop("conscio", None)
            ts = time.strftime("%Y%m%d-%H%M%S")
            hostcfg.backup_then_write_json(
                json_path,
                mutate=lambda o: o.update(
                    mcpServers=claude_json_data.get("mcpServers", {})),
                verify=lambda o: "conscio" not in o.get("mcpServers", {}),
                ts=ts)
    except OSError as exc:
        io.echo(f"legacy cleanup failed: {exc}")
        return 1

    io.echo(f"moved {moved} path(s) to {backup_root}")
    io.echo("restore any single file with: rename it back under ~/.claude")
    return 0
