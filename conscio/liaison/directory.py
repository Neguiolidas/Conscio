# conscio/liaison/directory.py
"""Directory of relay peers — public address cards on disk (v4.5.4 C2).

Cada agente publica um cartão em ~/.conscio/relay/peers/<id>.json. O cartão é
ENDEREÇO PÚBLICO, nunca segredo: quem lê descobre para onde depositar mensagem.
Engine-free e sem sqlite de propósito — o diretório precisa funcionar mesmo com
o banco do agente ausente, senão a descoberta morre junto com o banco.

Vivacidade NÃO filtra o catálogo: peer parado continua endereçável porque o
spool é arquivo, não sessão. Filtrar aqui repetiria a classe de bug A1/A2
(allowlist que esvazia e vira nega-tudo)."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .agents import STALE_AFTER_S

RELAY_ROOT_ENV = "CONSCIO_RELAY_ROOT"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PRUNE_AFTER_DAYS = 30.0

__all__ = [
    "RELAY_ROOT_ENV",
    "STALE_AFTER_S",
    "forget",
    "get",
    "is_live",
    "peers",
    "peers_dir",
    "prune",
    "publish",
    "relay_root",
    "spool_dir",
    "valid_id",
]


def relay_root() -> Path:
    """Praça pública da máquina: só endereço, nunca estado privado."""
    raw = os.environ.get(RELAY_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".conscio" / "relay"


def valid_id(instance_id: str) -> bool:
    """Id vira nome de diretório e de arquivo: valida ANTES do filesystem (I1).

    `handle_inbound` recebe o destinatário pela REDE — sem esta porta, um
    `to` com `../` escreve fora do spool root.
    """
    return bool(_ID_RE.match(instance_id or ""))


def _require_id(instance_id: str) -> str:
    if not valid_id(instance_id):
        raise ValueError(f"instance_id invalido para caminho: {instance_id!r}")
    return instance_id


def peers_dir() -> Path:
    return relay_root() / "peers"


def spool_dir(instance_id: str) -> Path:
    return relay_root() / "spool" / _require_id(instance_id)


def _card_path(instance_id: str) -> Path:
    return peers_dir() / f"{_require_id(instance_id)}.json"


def _write_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def publish(card: dict) -> None:
    """Publica o cartão do próprio agente.

    Levanta OSError/ValueError de proposito: quem chama decide se engole, mas
    o erro NAO pode sumir sem ninguem saber — agente invisivel que se acha
    visivel e a falha silenciosa que a regra R1 proibe.
    """
    cid = _require_id(str(card.get("instance_id", "")))
    payload = dict(card)
    payload["instance_id"] = cid
    payload.setdefault("updated_at", time.time())
    _write_atomic(_card_path(cid), json.dumps(payload, ensure_ascii=False))


def get(instance_id: str) -> dict | None:
    if not valid_id(instance_id):
        return None
    try:
        card = json.loads(_card_path(instance_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return card if isinstance(card, dict) else None


def peers(exclude: str = "") -> list[dict]:
    """Catalogo de enderecos, sem filtro de vivacidade."""
    try:
        entries = sorted(peers_dir().glob("*.json"))
    except OSError:
        return []
    out: list[dict] = []
    for path in entries:
        if path.name.startswith("."):
            continue
        cid = path.stem
        if not valid_id(cid) or cid == exclude:
            continue
        card = get(cid)
        if card and card.get("instance_id") == cid:
            out.append(card)
    return out


def is_live(card: dict | None, now: float | None = None) -> bool:
    """So para exibicao (observatory, alive_only) — nunca para endereçar."""
    if not card:
        return False
    try:
        ts = float(card.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        return False
    return (time.time() if now is None else now) - ts <= STALE_AFTER_S


def forget(instance_id: str) -> bool:
    try:
        _card_path(instance_id).unlink()
        return True
    except (OSError, ValueError):
        return False


def prune(max_age_days: float = PRUNE_AFTER_DAYS) -> int:
    """Coleta cartao de agente extinto."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for card in peers():
        try:
            ts = float(card.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts < cutoff and forget(str(card.get("instance_id", ""))):
            removed += 1
    return removed
