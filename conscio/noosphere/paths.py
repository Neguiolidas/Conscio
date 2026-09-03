# conscio/noosphere/paths.py
"""Filesystem layout. Neutral by default — Conscio is an agent framework, not a
Hermes-only tool.

The home dir resolves as: ``CONSCIO_HOME`` (explicit, neutral) > ``HERMES_HOME``
(legacy override) > an auto-detect that preserves an existing ``~/.hermes``
install and otherwise defaults to ``~/.conscio``. This keeps pre-existing
installs on their current layout while giving fresh agents a neutral home that
has nothing to do with the Hermes agent.
"""
from __future__ import annotations

import os
from pathlib import Path


def _neutral_default() -> Path:
    """The neutral home: ``~/.conscio``. Used when nothing explicit is set and no
    legacy ~/.hermes install is detected."""
    return Path(os.environ.get("CONSCIO_HOME",
                               Path.home() / ".conscio")).expanduser()


def conscio_home() -> Path:
    """Resolve the Conscio home dir, neutral-first with legacy override.

    Precedence (deterministic — no filesystem detection):
      1. ``CONSCIO_HOME`` — the explicit neutral override.
      2. ``HERMES_HOME`` — explicit legacy override (still supported).
      3. otherwise ``~/.conscio`` (the neutral default).

    ``expanduser`` is applied because a value read from the environment is
    whatever a shell/systemd/wrapper exported, and ``X=~/.x`` nothing expanded is
    a *relative* path to a literal ``~`` directory under the cwd.
    """
    if os.environ.get("CONSCIO_HOME"):
        return Path(os.environ["CONSCIO_HOME"]).expanduser()
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]).expanduser()
    return _neutral_default()


# Deprecated alias — retained so callers that still import ``hermes_home`` don't
# break; new code should import ``conscio_home``.
def hermes_home() -> Path:
    return conscio_home()


def default_storage() -> Path:
    return conscio_home() / "consciousness"


def default_noosphere_db() -> Path:
    return conscio_home() / "noosphere.db"


def resolve_storage(storage: str | os.PathLike[str] | None) -> Path:
    return Path(storage) if storage else default_storage()


def resolve_noosphere(noosphere: str | os.PathLike[str] | None) -> Path:
    return Path(noosphere) if noosphere else default_noosphere_db()


def instance_path(storage: str | os.PathLike[str]) -> Path:
    return Path(storage) / "instance.json"


def conscio_db_path(storage: str | os.PathLike[str]) -> Path:
    return Path(storage) / "conscio.db"


def quarantine_db_path(storage: str | os.PathLike[str]) -> Path:
    return Path(storage) / "noosphere_quarantine.db"