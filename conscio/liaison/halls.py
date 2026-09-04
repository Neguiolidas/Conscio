# conscio/liaison/halls.py
"""Agent's Hall — named groups of agents over the public relay directory (v4.5.4).

No shared roster. Two facts, two owners — and no file with two writers:

  "eu participo deste hall"   -> `halls` no cartão do agente      (o agente)
  "eu recuso este hall"       -> `halls_declined` no cartão       (o agente)
  "fulano está no hall"       -> `members` no doc do hall         (o dono)
  "fulano é o revisor"        -> `functions` no doc do hall       (o dono)

Membership efetiva = (declarados ∪ importados) − recusados. Entrada é plana:
todo agente entra como `executor` e o líder atribui a função depois, escrevendo
o doc dele — nunca o cartão alheio. `<relay_root>/halls/<hall_id>.json`.

Pure plumbing (like `directory`): engine-free, sem transporte próprio — quem
entrega é o `send` injetado em `send_to_hall`. Um peer quebrado nunca aborta
o fan-out. `sqlite3` sobrevive só dentro de `migrate_from_db`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from . import directory

# Vocabulário de FUNÇÃO dentro do hall — o que o agente faz ali. Não confundir
# com o `papel` do cartão (executor/orchestrator), que é governança da
# sociedade e passa por roles.normalize. Este conjunto NUNCA passa por lá.
FUNCTIONS = {
    "leader",           # convoca, atribui função, consolida o veredito
    "reviewer",         # procura o que passou despercebido, adversarialmente
    "architect",        # fronteiras, invariantes, acoplamento
    "security",         # superfície de ataque, segredos, permissões
    "optimizer",        # custo, latência, complexidade
    "tester",           # casos-limite, o que o teste verde não prova
    "researcher",       # busca fato e fonte fora do contexto de todo mundo
    "scribe",           # registra a decisão e fecha a deliberação
    "devils_advocate",  # dissenso obrigatório, mesmo quando todos concordam
    "executor",         # faz a mudança (padrão de entrada)
    "observer",         # recebe e não delibera (monitoramento, agente remoto)
}
DEFAULT_FUNCTION = "executor"
_LEGACY_FUNCTIONS = {"dono": "leader", "membro": DEFAULT_FUNCTION}


def _slugify(name: str) -> str:
    """ASCII, lowercase, alnum + hyphen. Non-word chars collapse to '-'.
    Returns '' for an empty/whitespace name."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _full_slug(owner: str, name: str) -> str:
    """owner--name: the owner namespaces the slug so two owners can both have
    a "team" without colliding (D6)."""
    d = _slugify(owner)
    n = _slugify(name)
    if not n:
        return ""
    return f"{d}--{n}" if d else n


def normalize_function(function: str | None) -> str:
    """Desconhecida LEVANTA. `roles.normalize` devolveria 'executor' calado e
    o conselho inteiro viraria uma fila de executores sem ninguém notar."""
    raw = (function or DEFAULT_FUNCTION).strip().lower().replace("-", "_")
    f = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    if f not in FUNCTIONS:
        raise ValueError(f"unknown hall function: {function!r}; "
                         f"known: {sorted(FUNCTIONS)}")
    return f


def halls_dir() -> Path:
    return directory.relay_root() / "halls"


def _require_hall_id(hall_id: str) -> str:
    if not directory.valid_id(hall_id):          # I1: valida ANTES do filesystem
        raise ValueError(f"invalid hall_id (empty or >64 chars): {hall_id!r}")
    return hall_id


def hall_doc_path(hall_id: str) -> Path:
    return halls_dir() / f"{_require_hall_id(hall_id)}.json"


def _write_doc(doc: dict) -> None:
    halls_dir().mkdir(parents=True, exist_ok=True)
    directory.write_atomic(hall_doc_path(doc["hall_id"]),
                           json.dumps(doc, ensure_ascii=False))


def _write_doc_new(doc: dict) -> bool:
    """Create the doc, or return False if it already exists.

    exists()-then-write is a check-then-act: two of the owner's own processes
    (an MCP call and a daemon tick) could both see nothing and the second
    replace would drop whoever had already joined. The filesystem decides,
    once, with O_EXCL.
    """
    halls_dir().mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    try:
        fd = os.open(hall_doc_path(doc["hall_id"]),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    return True


def create_hall(*, owner: str, name: str, policy: str = "open",
                invited: list[str] | None = None) -> dict | None:
    """Doc escrito só pelo dono (I9). None = duplicata; id inválido levanta."""
    hall_id = _require_hall_id(_full_slug(owner, name))
    if directory.get(owner) is None:
        # sem cartão do dono o hall nasceria sem dono dentro: falha alto em vez
        # de devolver um doc que ninguém habita
        raise ValueError(f"owner card not published: {owner!r}")
    doc = {"hall_id": hall_id, "name": name, "owner": owner,
           "created_at": time.time(), "policy": policy,
           "invited": list(invited or []), "members": [],
           "functions": {owner: "leader"}}
    if not _write_doc_new(doc):
        return None                      # already exists: not mine to replace
    join(instance_id=owner, hall_id=hall_id)
    return doc


def get_hall(hall_id: str) -> dict | None:
    if not directory.valid_id(hall_id):
        return None
    try:
        doc = json.loads(hall_doc_path(hall_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                      # doc ausente = hall aberto, não erro
    return doc if isinstance(doc, dict) else None


def list_halls(owner: str | None = None) -> list[dict]:
    try:
        paths = sorted(halls_dir().glob("*.json"))
    except OSError:
        return []
    out = [d for p in paths if (d := get_hall(p.stem)) is not None]
    if owner:
        out = [d for d in out if d.get("owner") == owner]
    return sorted(out, key=lambda d: d.get("created_at", 0), reverse=True)


def _set_membership(instance_id: str, hall_id: str, *, joining: bool) -> bool:
    """Read-modify-write do MEU cartão. Um escritor, sem lock (I9).
    Sair grava recusa explícita: ela vence a importação do líder — ele monta o
    hall, mas não me obriga a ficar nele."""
    _require_hall_id(hall_id)
    card = directory.get(instance_id)
    if card is None:
        return False                     # sem cartão não há o que declarar
    joined = [h for h in (card.get("halls") or []) if h != hall_id]
    declined = [h for h in (card.get("halls_declined") or []) if h != hall_id]
    (joined if joining else declined).append(hall_id)
    card["halls"] = sorted(joined)
    card["halls_declined"] = sorted(declined)
    card["updated_at"] = time.time()
    directory.publish(card)
    return True


def join(*, instance_id: str, hall_id: str) -> bool:
    """Sem função: todo mundo entra como executor. Quem promove é o líder."""
    return _set_membership(instance_id, hall_id, joining=True)


def leave(*, instance_id: str, hall_id: str) -> bool:
    return _set_membership(instance_id, hall_id, joining=False)


def _require_owner(hall_id: str, owner: str) -> dict:
    doc = get_hall(hall_id)
    if doc is None:
        raise ValueError(f"no such hall: {hall_id!r}")
    if doc.get("owner") != owner:
        raise PermissionError(f"only the owner writes the hall doc (I9): "
                              f"{doc.get('owner')!r} != {owner!r}")
    return doc


def set_function(*, hall_id: str, owner: str, instance_id: str,
                 function: str) -> bool:
    """O líder atribui função escrevendo o doc DELE, nunca o cartão alheio."""
    doc = _require_owner(hall_id, owner)
    doc.setdefault("functions", {})[instance_id] = normalize_function(function)
    _write_doc(doc)
    return True


def import_members(*, hall_id: str, owner: str,
                   instance_ids: list[str]) -> int:
    """Monta o hall sem ninguém configurar nada — o membro nem precisa estar
    vivo. Quem não quiser fica de fora com `leave` (recusa no cartão dele)."""
    doc = _require_owner(hall_id, owner)
    members = list(doc.get("members") or [])
    fresh = [i for i in instance_ids
             if directory.valid_id(i) and i not in members]
    if fresh:
        doc["members"] = members + fresh
        _write_doc(doc)
    return len(fresh)


def transfer_owner(*, hall_id: str, current_owner: str,
                   new_owner: str) -> bool:
    """Liderança muda; `hall_id` não. O prefixo `owner--` é namespace do id de
    criação, não declaração de quem manda — renomear quebraria todo cartão que
    já aponta para o hall."""
    if not directory.valid_id(new_owner):
        raise ValueError(f"invalid new owner id: {new_owner!r}")
    doc = _require_owner(hall_id, current_owner)
    if new_owner == current_owner:
        return True                           # idempotent, nothing to write
    # You hand leadership to someone who is in the room. A typo used to be
    # accepted and left the hall permanently unowned: nobody can pass
    # _require_owner again, so nobody can ever transfer it back.
    known = {m["instance_id"] for m in members_of(hall_id)}
    known |= {str(i) for i in (doc.get("members") or [])}
    if new_owner not in known:
        raise ValueError(
            f"new owner {new_owner} is not a member of {hall_id}; "
            "import or wait for them to join before transferring")
    doc["owner"] = new_owner
    doc.setdefault("functions", {})[new_owner] = "leader"
    _write_doc(doc)
    return True


def members_of(hall_id: str, *, alive_only: bool = False) -> list[dict]:
    """Uma varredura do diretório. Sem sqlite, sem JOIN, sem roster.
    Membros = (declarados ∪ importados) − recusados.
    Função = o que o doc do líder disser; executor na ausência."""
    if not directory.valid_id(hall_id):
        return []
    doc = get_hall(hall_id) or {}
    imported = {str(i) for i in (doc.get("members") or [])}
    assigned = doc.get("functions") or {}
    out: list[dict] = []
    for card in directory.peers():            # peers() inclui o próprio agente
        cid = card["instance_id"]
        if hall_id in (card.get("halls_declined") or []):
            continue                          # autonomia vence importação
        declared = hall_id in (card.get("halls") or [])
        if not declared and cid not in imported:
            continue
        alive = directory.is_live(card)
        if alive_only and not alive:
            continue
        out.append({"hall_id": hall_id, "instance_id": cid,
                    "function": assigned.get(cid, DEFAULT_FUNCTION),
                    "imported": not declared,
                    # fronteira do legado pt-BR: lê `modelo`, entrega `model`
                    "model": card.get("modelo", ""),
                    "family": card.get("familia", ""), "alive": alive,
                    "updated_at": card.get("updated_at", 0)})
    return sorted(out, key=lambda m: m["instance_id"])


def send_to_hall(*, from_instance: str, hall_id: str, type: str, payload: dict,
                 send, identity: dict | None = None,
                 function: str | None = None) -> int:
    """Fan-out para todo membro menos o remetente. `send` é injetado — o hall
    resolve nome, nunca transporta (Task 6 troca só o que é passado aqui).
    `function` endereça um subconjunto: convocar só os revisores é uma mensagem
    para os revisores, não uma para todos que os outros aprendem a ignorar."""
    target_fn = normalize_function(function) if function else None
    doc = get_hall(hall_id)
    allowed: set[str] | None = None
    if doc and doc.get("policy") == "invite":
        allowed = set(doc.get("invited") or []) | {doc.get("owner", "")}
    delivered = 0
    for m in members_of(hall_id):
        target = m["instance_id"]
        if target == from_instance:
            continue
        if target_fn is not None and m["function"] != target_fn:
            continue
        if allowed is not None and target not in allowed:
            continue
        try:
            send(from_instance=from_instance, to_instance=target, type=type,
                 payload=payload, identity=identity,
                 hall={"id": hall_id, "function": m["function"]})
            delivered += 1
        except Exception:
            continue                      # isolamento por peer: um ruim não aborta
    return delivered


def migrate_from_db(db: Path, self_id: str) -> int:
    """Uma vez, idempotente. Migro a MINHA membership e os halls que EU dono —
    a linha do outro agente é ele que carrega quando rodar isto."""
    if not Path(db).exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        rows = conn.execute("SELECT hall_id FROM hall_members"
                            " WHERE instance_id=?", (self_id,)).fetchall()
        mine = conn.execute("SELECT hall_id, nome, dono, criado_em FROM halls"
                            " WHERE dono=?", (self_id,)).fetchall()
        assigns = conn.execute("SELECT hall_id, instance_id, papel FROM"
                               " hall_members").fetchall()
    except sqlite3.Error:
        return 0                          # schema antigo ausente = nada a migrar
    finally:
        conn.close()
    # Única exceção ao "desconhecido levanta": dado legado que não controlamos
    # não pode travar o startup. Mapeia o que conhece, o resto vira padrão.
    for hall_id, name, owner, created_at in mine:
        if not directory.valid_id(hall_id) or hall_doc_path(hall_id).exists():
            continue
        fns = {i: _LEGACY_FUNCTIONS.get(p or "", DEFAULT_FUNCTION)
               for h, i, p in assigns if h == hall_id}
        fns[owner] = "leader"
        _write_doc({"hall_id": hall_id, "name": name, "owner": owner,
                    "created_at": created_at, "policy": "open",
                    "invited": [], "members": sorted(fns), "functions": fns})
    card = directory.get(self_id)
    if card is None:
        return 0
    joined = list(card.get("halls") or [])
    fresh = [h for (h,) in rows if directory.valid_id(h) and h not in joined]
    if fresh:
        card["halls"] = sorted(joined + fresh)
        card["updated_at"] = time.time()
        directory.publish(card)
    return len(fresh)
