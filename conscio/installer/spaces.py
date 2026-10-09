"""Per-host space binding: a stable slug -> ~/.conscio/instances/<slug>/ with
its own instance.json (identity), conscio.db, sandbox, and keys/ vault."""
from __future__ import annotations

import errno
from .. import filelock
import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from ..noosphere.identity import Identity, load_or_create

logger = logging.getLogger(__name__)

_HELD_MINTING_LOCKS: dict[str, int] = {}


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


@contextmanager
def minting_lock(slug: str, timeout: float = 5.0):
    """Exclusive flock for minting and adopting spaces.

    Serializes creation and pointer updates for ~/.conscio/instances/<slug>,
    preventing concurrent boot races on identity generation.
    """
    count = _HELD_MINTING_LOCKS.get(slug, 0)
    if count > 0:
        _HELD_MINTING_LOCKS[slug] = count + 1
        try:
            yield
        finally:
            _HELD_MINTING_LOCKS[slug] -= 1
            if _HELD_MINTING_LOCKS[slug] <= 0:
                _HELD_MINTING_LOCKS.pop(slug, None)
        return

    lock_file = INSTANCES_ROOT() / f".minting-{slug}"
    INSTANCES_ROOT().mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
    has_flock = False
    start_time = time.monotonic()
    try:
        while True:
            try:
                filelock.lock(fd, nonblocking=True)
                has_flock = True
                break
            except OSError as exc:
                if exc.errno in (errno.ENOLCK, errno.EOPNOTSUPP):
                    # Filesystem does not support flock (e.g. WSL drvfs/NFS)
                    has_flock = False
                    logger.warning(
                        "minting flock is not available on this filesystem (%s); "
                        "proceeding without exclusive lock via re-read only for %s",
                        errno.errorcode.get(exc.errno, exc.errno),
                        slug,
                    )
                    break
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise
                if time.monotonic() - start_time >= timeout:
                    raise TimeoutError(
                        f"space minting in progress for {slug} (lock acquisition timed out). "
                        "Refusing to proceed without exclusive minting lock. "
                        "Re-run when current minting finishes."
                    ) from None
                time.sleep(0.05)

        _HELD_MINTING_LOCKS[slug] = 1
        try:
            yield
        finally:
            _HELD_MINTING_LOCKS.pop(slug, None)
    finally:
        if has_flock:
            try:
                filelock.unlock(fd)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass


def ensure_space(slug: str) -> tuple[Path, Identity, bool]:
    d = space_dir(slug)
    with minting_lock(slug):
        inst_file = d / "instance.json"
        created = not inst_file.exists()
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

