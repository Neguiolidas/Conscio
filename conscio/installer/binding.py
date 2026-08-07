"""R6: validate that a --storage binding points at a real space before the
engine silently mkdir()s a blank one. Advisory only — never raises. Logs at
WARNING so the message is visible on the terminal, not just in debug."""
from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger("conscio.installer.binding")


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
