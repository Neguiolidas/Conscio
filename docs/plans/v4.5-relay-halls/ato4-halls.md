# Ato 4 — Agent's Hall (halls.py + MCP tools)

## Objetivo
O hall resolve o nó que você apontou: na sua máquina, agentes partem da mesma instalação,
e o rastreamento de quem é quem cria confusão. O Hall dá **agrupamento nomeado + dono**:
cada agente pode criar um Hall, convidar outros, e dirigir a conversa a um subconjunto —
mesmo que os agentes compartilhem a mesma `liaison.db` e "instalação" do ponto de vista do
sistema operacional. É o agrupador LÓGICO sobre o mesmo transporte físico.

## Protocol A (pre-flight já feito)
- `mailbox.py` single table messages; `agents` single table. Sem noção de grupo.
- Padrão de tools MCP: defs em `schemas.py` (RELAY_TOOL_DEFS), registração em `server.py`
  com flag (`--enable-relay`), método no Bindings.
- `mailbox.send` (Ato 1) tem `identity`; confirmado.
- Não existe nada de "hall" hoje.

## Entregas do Ato 4

### T1 — Módulo `conscio/liaison/halls.py` (novo, engine-free)
Duas tabelas no mesmo `liaison.db`:
```sql
CREATE TABLE IF NOT EXISTS halls (
    hall_id     TEXT PRIMARY KEY,    -- slug único (dono, slug) → gerar no create
    nome        TEXT NOT NULL,
    dono        TEXT NOT NULL,       -- instance_id de quem criou
    criado_em   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS hall_members (
    hall_id     TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    papel       TEXT NOT NULL DEFAULT 'membro',   -- dono | membro
    entrou_em   REAL NOT NULL,
    PRIMARY KEY (hall_id, instance_id)
);
```
API engine-free (espelha `agents`):
- `create_hall(db, *, dono, nome, slug=None) -> dict | None` — gera slug do dono+nome; se
  o hall já existe, retorna None (não duplica). Cria tabelas via `_conn`+DDL.
- `get_hall(db, hall_id) -> dict | None`
- `list_halls(db, *, dono=None) -> list[dict]`
- `add_member(db, *, hall_id, instance_id, papel='membro') -> bool`
- `remove_member(db, *, hall_id, instance_id) -> bool`
- `is_member(db, *, hall_id, instance_id) -> bool`
- `members_of(db, hall_id, *, alive_only=False) -> list[dict]` — com `alive_only` cruza
  com `agents.is_alive` p/ só membros presentes.
- `halls_of(db, instance_id) -> list[dict]` — halls em que o agente é membro.
- `send_to_hall(db, *, from_instance, hall_id, type, payload, identity=None) -> int` —
  fan-out pra cada membro (exceto remetente) via `mailbox.send`; retorna nº de entregues.
- Todos "Never raises" como `agents` (db quebrado → None/[]/0/False).

_conn próprio de halls (não reusar `agents._conn` — mirror helper, evita import cycle):
```
def _conn(db, *, read_only=False) -> sqlite3.Connection | None:
    # idêntico ao padrão agents._conn, mas com DDL de halls
```

Testes (test_liaison_halls.py novo):
- create_hall slug gerado, único, dono gravado, criado_em set.
- create_hall duplicado → None.
- add_member/remove_member/is_member/halls_of/members_of round-trip.
- members_of(alive_only=True) exclui membros com heartbeat velho.
- send_to_hall fan-out N membros (remetente excluído), retorna nº entregue.
- db quebrado → todas degradam (None/[]/0/False, sem crash).

### T2 — MCP tools `conscio_hall_*`
Flags novas no argparse: `--can-create-halls` (store_true). Quando ativo, registra tools:
- `conscio_hall_create` {nome} → cria hall (dono = self id)
- `conscio_hall_list` {} → halls visíveis (que ele é membro ou dono)
- `conscio_hall_join` {hall_id} → adiciona self como membro
- `conscio_hall_leave` {hall_id} → remove self
- `conscio_hall_send` {hall_id, type, payload} → send_to_hall
- `conscio_hall_members` {hall_id} → lista membros (com modelo, se agents tiver)
TODOS fail-soft (self_instance_id vazio → {ok:False, motivo}).

Schemas em `schemas.py` (padrão RELAY_TOOL_DEFS): HALL_TOOL_DEFS. Registração em server.py
atrás de `--can-create-halls` (espelha RELAY_TOOL_DEFS na linha ~319).

Testes (append test_liaison_bindings.py):
- registrar tools hall? → presente quando --can-create-halls, ausente sem.
- hall_create → retorna hall_id; send_to_hall → fan-out.
- self_instance_id vazio → ok False sem crash.

### T3 — Integração observatory (data only; visual é Ato 5)
Nenhuma aqui além do que o Ato 5 usa: halls.py expõe `list_halls`/`members_of`. Ato 5
projeta read-only. (Deixar claro: NÃO fazer visual neste ato.)

## Missing edge → não deixar para "próximo update"
- Hall com slug colidindo (dois donos criam "team") → slug leva dono (`dono--nome`),
  evitando colisão global. Decisão: incluir dono no slug.
- Fan-out para membro offline: `send_to_hall` entrega msm assim (mailbox guarda em disco,
  o leitor offline pega depois). Não filtrar por alive no envio — só na LEITURA (members
  display). Envio não bloqueia por presença.
- Remoção de hall: só dono pode `remove_member` de outro; agente sempre pode sair.
  (delete_hall inteiro fica para quando houver pedido — YAGNI agora, mas doc.)
- Convidar: sem mecanismo de convite formal neste ato — qualquer agente pode `join` num
  hall que conheça o hall_id. Controle fino (convite/invite-only) é decisão futura, NÃO
  pendência silenciosa.

## Regras de commit
- ruff check --fix halls.py + schemas.py + server.py + 2 arquivos de teste.
- Testes nominais: `tests/test_liaison_halls.py`, `tests/test_liaison_bindings.py`.
- Um processo por teste: `python -m pytest tests/test_liaison_halls.py -q`.
- Commit: `feat(liaison): Agent's Hall - grupos nomeados de agentes + tools MCP`

## Self-Review (Protocol G) — PASS
Rodado contra codebase real (2026-08-29):
- CRITICAL FIXED: padrão de tools MCP confirmado — `if self.relay: tools["conscio_relay_*"]`
  (server.py ~319). Halls seguem o mesmo gate `--can-create-halls`. Schemas importado
  (`from . import schemas`). ✓
- CRITICAL FIXED: NÃO há colisão de nome — o "hall" existente é `conscio.hallways`
  (WingManager/memória espacial, db hallways.db), conceito distinto. Agent's Hall é novo.
  Nada a renomear. ✓
- CRITICAL FIXED: `mailbox.send(identity=)` (Ato 1) é a base do `send_to_hall` fan-out.
- MEDIUM FIXED: slug `dono--nome` evita colisão entre dois donos criando "team".
- NÃO REGRESSA: fan-out entrega mesmo a membro offiline (mailbox guarda em disco);
  presença filtra só na LEITURA (members_of alive_only).
Status: PASS.

## Nota de sequência
Depende do Ato 1 (envelope) e Ato 3 (identity no Bindings). Hall agrupa agents que já
estão no registro. Roda depois do Ato 3.