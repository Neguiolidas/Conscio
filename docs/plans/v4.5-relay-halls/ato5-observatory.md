# Ato 5 — Observatory: /api/agents + /api/halls + view tempo real

## Objetivo
Visual no Observatory do registro de agentes e dos Halls: quem está vivo (com modelo/ID),
quais Halls existem, quem é membro de cada um, mailboxes separados — atualizado em tempo
real (poll curto). Projeção read-only, engine-free, como `society.py`/`liaison_view.py`.

## Protocol A (pre-flight já feito)
- Padrão view: `SocietyProjection` com `_ro()` (mode=ro) + `_select(sql, params)` (society.py:22-39).
  `LiaisonProjection` idêntico (liaison_view.py).
- Rotas no `server.py` do observatory: `/api/society/members`, `/api/relay/inbox` já existem.
- `agents` table tem instance_id/model/status/capabilities/last_heartbeat (+identity cols do Ato 1).
- `halls` + `hall_members` tables do Ato 4.
- Static: observatory/static tem os assets da UI existente.

## Entregas do Ato 5

### T1 — `conscio/observatory/halls_view.py` (novo, mirror liaision_view)
Classe `HallsProjection`:
- `_ro()` / `_select()` — copia o padrão exato de society.py (NÃO importa os helpers —
  mirror, evita puxar write logic).
- `agents(liaison_db, *, include_stale=False) -> list[dict]` — projeta agents + traduz
  capabilities CSV → lista; filtra stale por default (vivos primeiro).
- `halls(liaison_db, *, dono=None) -> list[dict]` — halls + contagem de membros.
- `hall_members(liaison_db, hall_id, *, alive_only=True) -> list[dict]` — membros com
  modelo/status, filtra vivo por default.
- `mailboxes(liaison_db, self_id) -> list[dict]` — por agente: id, unread_count dirigido
  (reusa query similar a liaison_view.inbox mas agrega por from).
- Todas pedentes NUNCA levantam (db ausente/table absent → []).

Testes (test_observatory_halls.py novo):
- agents() com rows → payload traduzido, capabilities como lista, stale filtrado por default.
- halls() + hall_members() round-trip com as tables do Ato 4.
- mailboxes() agrega não-lidos por emissor.
- db sem tables → [] (sem crash).
- Assert read-only: após chamar projeção, o db não sofreu write (WAL não criado / tamanho ok).

### T2 — Rotas no observatory server
Rotas novas espelhando as existentes:
- GET `/api/agents` → HallsProjection.agents() global.
- GET `/api/halls` → halls() com members_of aninhado.
- GET `/api/halls/{hall_id}/members` → hall_members().
- GET `/api/mailboxes` → mailboxes() global.

Implementadas no `observatory/server.py` na mesma convenção das rotas /api/society/* e
/api/relay/inbox. Read-only — nenhuma rota escreve.

### T3 — View/refresh tempo real (frontend do observatory)
O static do observatory ganha uma view "Relay · Halls" alimentada por essas rotas:
- Poll `/api/agents` + `/api/halls` a cada ~3-5s (setInterval simples, no JS do observatory).
- Render: lista de Halls (nome/dono/membros), ao lado a lista de agents vivos (ID · modelo ·
  status), e mailbox (aggerado por emissor, não-lidos em destaque).
- Atualiza em tempo real conforme o poll (sem SSE — poll curto é suficiente e mais simples;
  usuário pediu "tempo real se possível", poll 3s atende sem adicionar transporte).

Constrangimento: reeditar o JS static do observatory seguindo o padrão já usado nas outras
views (não importa framework novo).

Testes:
- View renderiza os 3 blocos com data mock (fetch stub);
- Poll invoca refresh a cada N segundos (fake timer).

## Missing edge → não deixar para "próximo update"
- Stale agent NÃO some da view: fica com status "offline" e dim-out (não desaparece) —
  o dono precisa VER quem sumiu, não só quem está. `include_stale=True` na view, com flag
  `offline: true` no row.
- Longa lista: paginação/limite nas rotas (clamp, padrão clamp_int). Não abrir estoque
  infinito.
- Hall vazio: aparece (dono pode ver o próprio hall sem membros), não oculto.

## Regras de commit
- ruff check --fix halls_view.py + server.py + static JS + 1 teste.
- `python -m pytest tests/test_observatory_halls.py -q` (um processo).
- NÃO deployar observatory publicamente (fica local/loopback como hoje) — não é segredo,
  é decisão de escopo: visual é pro Senhor ver.
- Commit: `feat(observatory): Relay Halls - view tempo real de agents + halls + mailboxes`

## Self-Review (Protocol G) — PASS
Rodado contra codebase real (2026-08-29):
- CRITICAL FIXED: padrão de projeção confirmado — `society.py` `_ro()/select()` mode=ro,
  read-only. HallsProjection espelha em `halls_view.py`. ✓
- CRITICAL FIXED: static do observatory tem `app.js` (20KB, padrão de view) + `index.html`
  renderizado pelo server. View "Relay·Halls" segue app.js; poll setInterval. ✓
- CRITICAL FIXED: rota `/api/relay/inbox` existe em server.py → /api/agents + /api/halls
  entram na mesma convenção. ✓
- MEDIUM FIXED: rota usa `HTMLResponse` (não "Response(") — o plano referencia index.html.
- NÃO REGRESSA: view mostra stale como "offline" (não some); poll 3-5s sem SSE (simples).
Status: PASS.

## Nota de sequência
Depende do Ato 3 (agents no server) e Ato 4 (halls). Roda depois do Ato 4.