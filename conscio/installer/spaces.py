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


# ─── isolation por agente (hard-block cross-env)

def space_is_cross_agent(space: str, self_instance_id: str) -> bool:
    """True se ``space`` é dono de um AGENTE diferente do atual.

    Modelo: cada espaço tem um ``instance.json`` de identidade própria (criado
    por ``load_or_create``). O guard compara a identidade gravada no espaço com
    a identidade do agente que está tentando escrever (``self_instance_id`` —
    ex. ``CONSCIO_SELF_ID`` ou o instance_id do home do próprio agente).

    Se o espaço já tem um dono (instance.json existe) e esse dono NÃO é o self,
    então é um espaço de outro agente → HARD-BLOCK de escrita (leitura continua
    permitida). Sem instance_id próprio (espaço novo) → não é cross.

    Não depende de heurística de home dir, porque agentes distintos coabitam o
    mesmo home. Home é só onde o espaço mora; a autoridade é a identidade.
    """
    if not space or not self_instance_id:
        return False
    try:
        from ..noosphere.identity import NoosphereIdentityError, _read
        d = Path(space).expanduser().resolve()
    except (OSError, ValueError, TypeError):
        return False
    if not (d / "instance.json").exists():
        # Espaço ainda não tem dono — o primeiro a reivindicar decide.
        return False
    try:
        owner_id = _read(d / "instance.json").instance_id
    except NoosphereIdentityError:
        # identity corrompida = espaço não confiável; falha fechada.
        return True
    return owner_id != self_instance_id

def liaison_db_path(slug: str) -> Path:
    """Cada agente tem seu liaison.db privado DENTRO de instances/<slug>/.
    Compartilhamento entre agentes é feita SOMENTE via transport
    (HTTP tailscale), nunca pelo liaison.db partilhado.
    """
    return space_dir(slug) / "liaison.db"
