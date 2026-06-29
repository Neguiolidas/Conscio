"""Per-host space binding: a stable slug -> ~/.conscio/instances/<slug>/ with
its own instance.json (identity), conscio.db, sandbox, and keys/ vault."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..noosphere.identity import Identity, load_or_create


def _base() -> Path:
    return Path(os.environ.get("CONSCIO_BASE", str(Path.home() / ".conscio")))


def INSTANCES_ROOT() -> Path:
    return _base() / "instances"


def DAEMONS_ROOT() -> Path:
    return _base() / "daemons"


def slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.strip().lower())
    s = s.strip("-")
    return s or "default"


def space_dir(slug: str) -> Path:
    return INSTANCES_ROOT() / slug


def vault_dir(slug: str) -> Path:
    return space_dir(slug) / "keys"


def ensure_space(slug: str) -> tuple[Path, Identity, bool]:
    d = space_dir(slug)
    created = not (d / "instance.json").exists()
    d.mkdir(parents=True, exist_ok=True)
    ident = load_or_create(d)        # never regenerates an existing identity
    return d, ident, created
