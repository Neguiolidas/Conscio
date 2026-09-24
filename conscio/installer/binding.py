"""R6: validate that a --storage binding points at a real space before the
engine silently mkdir()s a blank one. Advisory only — never raises. Logs at
WARNING so the message is visible on the terminal, not just in debug.

`unexpanded_variable` is the one hard check, and it is not advisory: the
caller refuses to start. A literal `${CLAUDE_PLUGIN_DATA}/space` is not a
drifted space, it is a host that never substituted the variable (v4.7.2:
Codex ran the Claude Code plugin asset verbatim), and mkdir() on it creates
a phantom space relative to whatever the working directory happens to be."""
from __future__ import annotations

import logging
import re
from pathlib import Path

_log = logging.getLogger("conscio.installer.binding")

# ${NAME}, ${NAME:-default}, ${NAME:?msg} … and bare $NAME.
_UNEXPANDED = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def unexpanded_variable(storage) -> str | None:
    """Name of a variable left literal in a storage path, or None.

    Pure. A `$` followed by an identifier never names a directory anyone
    meant to create; it means the launching host skipped substitution."""
    if not isinstance(storage, str):
        return None
    m = _UNEXPANDED.search(storage)
    return (m.group(1) or m.group(2)) if m else None


def validate_binding(storage, *, log: logging.Logger | None = None) -> bool:
    log = log or _log
    if not storage:
        return True                       # default storage; nothing to validate
    try:
        d = Path(storage).expanduser()
    except TypeError:
        return True                       # unusable arg; don't block startup
    # A directory that is absent and one that is present-but-empty are the same
    # situation: nobody has made a space here yet. Container mounts, pre-created
    # paths and `mkdir -p` all produce the empty variant, so treating it as drift
    # would nag a fresh install forever and leave the space without an identity.
    if not d.exists() or _is_empty(d):
        try:
            from ..noosphere.identity import load_or_create
            d.mkdir(parents=True, exist_ok=True)
            load_or_create(d)
        except Exception as exc:          # advisory contract: never raise
            log.warning("storage binding %s could not be initialised (%s) — "
                        "run `conscio init --repair`.", d, exc)
            return False
        return True
    if not (d / "instance.json").exists():
        # Populated, but with no identity: either a space that lost its
        # instance.json or a --storage pointed at some unrelated directory.
        log.warning("storage binding %s has contents but no instance.json "
                    "(blank/space drift) — run `conscio init --repair`.", d)
        return False
    return True


def _is_empty(d: Path) -> bool:
    try:
        next(d.iterdir())
    except StopIteration:
        return True
    except OSError:                       # unreadable: not our call to make
        return False
    return False
