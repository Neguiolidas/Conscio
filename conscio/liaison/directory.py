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
# v4.7.2: silent this long, a LOCAL card stops receiving broadcasts. Measured
# 2026-09-24: 8fb1197f had been silent 20 days and was still collecting fan-out
# (10 messages parked, the newest 68h old) — nobody would ever read them. Three
# days clears a weekend away; a direct send still goes through, with a warning.
DORMANT_AFTER_S = 3 * 86400.0

__all__ = [
    "DORMANT_AFTER_S",
    "RELAY_ROOT_ENV",
    "STALE_AFTER_S",
    "dormant_for",
    "forget",
    "get",
    "is_live",
    "is_remote",
    "orphan_spools",
    "peers",
    "peers_dir",
    "prune",
    "publish",
    "publish_self",
    "relay_root",
    "spool_dir",
    "valid_id",
    "write_atomic",
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


def write_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
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
    write_atomic(_card_path(cid), json.dumps(payload, ensure_ascii=False))


def publish_self(instance_id: str, *, modelo: str | None = None,
                 familia: str | None = None,
                 runtime: str | None = None, papel: str | None = None,
                 capabilities: tuple[str, ...] | None = None,
                 url: str = "", space: str | None = None,
                 min_interval: float = 0.0) -> bool:
    """Publica/refresca MEU cartão. Devolve True se escreveu.

    Um agente é alcançável porque seu cartão existe, não porque ele está numa
    sessão. Por isso qualquer processo persistente do agente (MCP server OU
    reactor) mantém o cartão vivo — quem só roda o reactor descobre os outros
    e, sem isto, ficaria invisível para eles.

    `min_interval` pula a escrita se o cartão atual for mais novo que isso; o
    throttle sai do próprio cartão (e não de estado em memória) para que um
    restart não vire uma rajada de republicações.

    v4.6.8 — READ-MODIFY-WRITE COM SENTINELA. O contrato tem três faces,
    todas medidas contra o artefato 4.6.7 (três classes de apagamento):

    * ``None`` (default) = "não sei, preserva". Um escritor que não conhece o
      campo — o reactor republicando cego — nunca apaga o que o servidor que
      sabe escreveu. Vale para modelo/familia/runtime/papel/capabilities/space
      e para QUALQUER chave futura que já esteja no cartão.
    * Valor explícito (não-None) = "eu sei, escreve". O servidor que resolveu
      a identidade e o espaço vence o valor velho.
    * Chaves desconhecidas do cartão antigo sobrevivem por construção: o card
      NOVO nasce do VELHO, não do zero.

    Isto subsume as duas regras ad-hoc anteriores (o laço de halls e o bloco
    `herdado` do space) numa só: quem não passa, não mexe.
    """
    old = get(instance_id) or {}
    if min_interval > 0:
        age = time.time() - float(old.get("updated_at") or 0.0)
        if 0 <= age < min_interval:
            return False
    # v4.6.8: o card novo nasce do VELHO — chaves que esta versão não conhece
    # sobrevivem por construção, não por lista nominal.
    card = dict(old)
    card.update({
        "instance_id": instance_id,
        "spool": str(spool_dir(instance_id)),
        # cartão local nunca leva url: quem me alcança de fora usa o
        # remotes.json do lado dele (conscio relay pair).
        "url": url,
        "updated_at": time.time(),
    })
    # Sentinela: só quem sabe escreve. None = preserva o que já está no card.
    if modelo is not None:
        card["modelo"] = modelo
    if familia is not None:
        card["familia"] = familia
    if runtime is not None:
        card["runtime"] = runtime
    if papel is not None:
        card["papel"] = papel
    if capabilities is not None:
        card["capabilities"] = list(capabilities)
    # space: valor explícito vence (o servidor que RESOLVEU um espaço);
    # None/omitido preserva — o reactor cego herda, nunca apaga.
    if space is not None:
        card["space"] = space
    publish(card)
    return True


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


def is_remote(card: dict | None) -> bool:
    """Peer on another machine (written by `relay pair`): it has a url and no
    local spool. Nothing on THIS machine heartbeats it — its `updated_at` is
    the pairing time — so its age says nothing about whether it is alive."""
    return bool(card and card.get("url") and not card.get("spool"))


def _age(card: dict, now: float) -> float:
    try:
        return now - float(card.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        return float("inf")


def dormant_for(card: dict | None, now: float | None = None) -> float | None:
    """Seconds a LOCAL card has been silent past DORMANT_AFTER_S, else None.

    None for a missing card (a named or remote peer is not "dormant", it is
    unknown here) and for any remote card (see `is_remote`)."""
    if not card or is_remote(card):
        return None
    age = _age(card, time.time() if now is None else now)
    return age if age > DORMANT_AFTER_S else None


def orphan_spools() -> list[tuple[str, int]]:
    """(id, parked) for every spool holding messages for an id with NO card.

    Read-only, for `relay doctor`. Nobody ingests these: the card that would
    lead an agent to its spool is gone. Alias symlinks are skipped — they point
    at a real id's spool, which is counted under that id."""
    root = relay_root() / "spool"
    out: list[tuple[str, int]] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir() or not valid_id(entry.name):
            continue
        if get(entry.name) is not None:
            continue
        try:
            n = sum(1 for _ in entry.glob("*.json"))
        except OSError:
            continue
        if n:
            out.append((entry.name, n))
    return out


def forget(instance_id: str) -> bool:
    try:
        _card_path(instance_id).unlink()
        return True
    except (OSError, ValueError):
        return False


def prune(max_age_days: float = PRUNE_AFTER_DAYS) -> int:
    """Coleta cartao de agente extinto.

    v4.7.2: remote cards are never collected — their age is the pairing time,
    so pruning them would silently unpair a live machine 30 days after
    `relay pair`. And the age is re-read right before the unlink: an agent that
    comes back after a month republishes, and must not lose the fresh card to a
    decision taken on the stale one."""
    cutoff_s = max_age_days * 86400
    removed = 0
    for card in peers():
        cid = str(card.get("instance_id", ""))
        if is_remote(card) or _age(card, time.time()) <= cutoff_s:
            continue
        fresh = get(cid)
        if fresh is None or is_remote(fresh) or _age(fresh, time.time()) <= cutoff_s:
            continue
        if forget(cid):
            removed += 1
    return removed
