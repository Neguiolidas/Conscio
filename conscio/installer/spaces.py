"""Per-host space binding: a stable slug -> ~/.conscio/instances/<slug>/ with
its own instance.json (identity), conscio.db, sandbox, and keys/ vault."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..noosphere.identity import Identity, load_or_create


def _base() -> Path:
    return Path(os.environ.get(
        "CONSCIO_BASE", str(Path.home() / ".conscio"))).expanduser()


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


# ─── v4.6.0: isolation por agente (hard-blocks cross-env)

_OWNER_ENV_TAGS = ("HERMET", "CLAUDE", "GEMINI", "ANTIGRAVITY", "QWEN")

def _owner_tag_from_home(home: str) -> str:
    """Detecta o owner a partir do home dir (e.g. '/home/ubuntu' → 'UBUNTU').
    Agents thought profile dir names. For paths like /home/ubuntu/.gemini,
    the owner is the USER (e.g. /home/ubuntu), not the app (~/.gemini).
    """
    h = str(Path(home).expanduser().resolve())
    # extract the user from /home/<user>/ prefix
    parts = Path(h).parts
    if len(parts) >= 3 and parts[1] == "home":
        return parts[2].upper()
    if len(parts) >= 2 and parts[0] == "/" and parts[1] in ("root", "Users"):
        return Path(h).name.upper()
    return Path(h).stem.upper()

def space_is_cross_agent(space: str, owner_home: str) -> bool:
    """True se ``space`` pertence a outro agente (home dir diferente).
    HARD-BLOCK: cross-agent por home dir. Leitura cross-agente é permitida
    (consulta entre agents é okay). Detect via user home resolution.
    """
    if not space or not owner_home:
        return False
    try:
        owner_home_r = Path(owner_home).expanduser().resolve()
        space_r = Path(space).expanduser().resolve()
    except (OSError, ValueError, TypeError):
        return False
    # same user home? → not cross
    if str(space_r).startswith(str(owner_home_r) + os.path.sep):
        return False
    return True

def liaison_db_path(slug: str) -> Path:
    """Cada agente tem seu liaison.db privado DENTRO de instances/<slug>/.
    Compartilhamento entre agentes é feita SOMENTE via transport
    (HTTP tailscale), nunca pelo liaison.db partilhado.
    """
    return space_dir(slug) / "liaison.db"
