# conscio/noosphere/paths.py
"""Filesystem layout. Mirrors conscio.cli._storage exactly: HERMES_HOME
(default ~/.hermes); per-instance state under <storage> (default
$HERMES_HOME/consciousness); host-shared noosphere.db at $HERMES_HOME/noosphere.db."""
from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def default_storage() -> Path:
    return hermes_home() / "consciousness"


def default_noosphere_db() -> Path:
    return hermes_home() / "noosphere.db"


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
