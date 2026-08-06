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
    if not d.exists():                    # fresh plugin install: bootstrap it
        try:
            from ..noosphere.identity import load_or_create
            d.mkdir(parents=True, exist_ok=True)
            load_or_create(d)
        except Exception as exc:          # advisory contract: never raise
            log.warning("storage binding %s does not exist and could not be "
                        "created (%s) — run `conscio init --repair`.", d, exc)
            return False
        return True
    if not (d / "instance.json").exists():
        log.warning("storage binding %s has no instance.json (blank/space "
                    "drift) — run `conscio init --repair`.", d)
        return False
    return True
