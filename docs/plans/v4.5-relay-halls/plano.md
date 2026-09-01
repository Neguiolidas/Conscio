# Conscio v4.5 — Relay Reativo + Agent's Hall

## Goal
Transformar o relay de um canal de mensageria estático (allowlist fixa, poll não-reativo,
falha silenciosa, identidade só por ID) num mailbus **reativo, multi-agente paralelo, com
registro de presença, envelope de procedência por modelo, e Agent's Hall** com visual em
tempo real no Observatory.

## Arquitetura (resumo
- Camada `liaison`: ganha (i) integração relay→`agents` (já existe), (ii) envelope de
  procedência no `mailbox.send`, (iii) heartbeat de 3 estados (entregue/não-entregue/vivo),
  (iv) quarentena de payload malformado.
- Camada `agency`: watcher reativo (loop persistente) + suporte a conversas multi-agente
  paralelas (não só diálogo 1:1).
- Camada `observatory`: rotas `/api/halls` + `/api/agents` + view de Halls; visual
  atualizado em tempo real (poll curto / SSE).
- `mcp/server.py`: `self_id` + identidade (modelo/runtime/papel) derivados do runtime;
  peers vêm de `agents.list_agents()` em vez de `--relay-peer` fixo.

## Tech Stack
Python 3.10+, sqlite (WAL já em `mailbox._connect`), stdlib só. Zero novas deps.

## Standing constraints (não-negociáveis)
- Conscio/Neurata: pytest completo OOM a máquina local — cada passo de teste nomeia UM
  arquivo, um processo; só CI roda a suíte inteira.
- Nunca importar `conscio.engine` de dentro de `liaison/*` (invariante engine-free).
- `conscio.engine` nunca entra em `liaison/`; agência MCP chama os read-trio só.
- `ruff check --fix` em TODO arquivo novo/alterado antes do commit.
- Pyright 0 erros. Nome do arquivo de teste = espelho do módulo.
- Naming: nomes profissionais, positivos (nada de "parasita"). Version bump = decisão do
  Senhor; v4.5 proposto, confirmar antes de tagar.
- NUNCA push de tag/release sem autorização explícita.
- `.md` de plano fica em docs/ local — NUNCA commitar plano em git (só código/estado).

## Design decisions locked
- D1 — Registro reusa `conscio/liaison/agents.py` já existente (v4.1.1). Não recriar.
  Relay e MCP server passam a consultar `agents.list_agents()` p/ peers, mantendo
  `--relay-peer` como FALLBACK / seed inicial (compatibilidade retroativa, não autoridade).
- D2 — Envelope de procedência: `mailbox.send` ganha `identity: dict | None`; se não for
  passado, lê do `agents` row do emissor. Grava como `payload["_meta"] = {"from": identity,
  "ts": ..., "id": <msg id>}`. Runtime deriva identidade de env/args, nunca do agente.
- D3 — Heartbeat/3 estados: watcher passa a emitir `{estado, cursor, par, ts}`. Poll db
  malformado → estado "não_entregue: motivo Y", NÃO `[]` mudo. Quarentena: payload que
  falha `json.loads` mora numa tabela `quarantine(id, motivo)` e o resto entrega.
- D4 — Reatividade: watcher com `--interval` loop persistente (já existe rascunho em
  watcher.py:303). Desliga cron/rearm manual como caminho primário; cron vira fallback.
- D5 — Multi-agente paralelo: `agents` + `a2a.route_and_send` permitem N destinos.
  Conversa direcionada continua por `to_instance`; Hall é o agrupador lógico.
- D6 — Agent's Hall: nova tabela `halls(id, nome, dono, criado_em)` +
  `hall_members(hall_id, instance_id, papel_no_hall, entrou_em)`. Agente com bando
  `--can-create-halls` cria; demais são convidados. Envio p/ Hall = fan-out p/ membros.
- D7 — Observatory `/api/halls` + `/api/agents`: projeção read-only (padrão `liaison_view`),
  polling curto no client. Roda fora do engine (observatory não importa engine).
- D8 — `self_id` continua `CONSCIO_SELF_ID`/`--self_id` (não mudar contrato da sessão);
  identidade de MODELO (familia/runtime/papel) vai no envelope e no `agents` row.

## Accepted trade-offs
- Fica o `--relay-peer` CLIs iguais (p/ não quebrar deploy em produção); o registro vira a
  fonte autoritativa quando presente. Convivência dos dois é um estado transitório legítimo.
- `agents.capabilities` já é string CSV no sqlite (sem array type) — routing parseia em
  memória. Trade-off já ratificado em v4.1.1, não vou "corrigir".
- Hall fan-out usa `mailbox.send` N vezes (não transação única) — 1 peer com falha não
  aborta o resto (isolação por peer, padrão já do broadcast em server.py:509).

## File Structure
| Arquivo | Responsabilidade |
|---|---|
| `conscio/liaison/mailbox.py` | usar `send(identity=)`, `quarantine()`, `list_quarantine` |
| `conscio/liaison/agents.py` | (existe) — garantir `familia/runtime/papel/nome` no row |
| `conscio/liaison/halls.py` | NOVO — tabelas halls + hall_members, CRUD, membros, fan-out |
| `conscio/liaison/relay.py` | validate_send com `peers` dinamico + is_relay_message ciente de identity |
| `conscio/liaison/watcher.py` | heartbeat 3-estados + loop reativo + quarentena |
| `conscio/mcp/server.py` | peers de `agents`, identity no send, tools `conscio_hall_*` |
| `conscio/observatory/server.py` | rotas `/api/halls`, `/api/agents` |
| `conscio/observatory/halls_view.py` | NOVO — projeção read-only de Halls+members |
| `docs/roadmap.md` | entry v4.5 |
| `conscio/__init__.py` + `pyproject.toml` | bump versão |
| tests/ | `test_liaison_halls.py`, `test_relay_envelope.py`, `test_watcher_heartbeat.py`, `test_mcp_hall.py`, `test_observatory_halls.py` |

## Atos (ordem de execução)
- Ato 0 — setup: registrar estado atual (git log), adicionar colunas no agents (familia/runtime/papel/nome), migração segura.
- Ato 1 — Envelope de procedência + quarentena (mailbox/relay).
- Ato 2 — Relays reativo: watcher heartbeat 3-estados + loop persistente + regist. presença.
- Ato 3 — MCP server: peers dinâmicos + identity + tools hall_* + self-register/heartbeat.
- Ato 4 — Agent's Hall: halls.py + tests + mcp tools.
- Ato 5 — Observatory: /api/agents + /api/halls + view + refresh tempo real.
- Ato 6 — Docs + bump versão + tags. (bump só com autorização)

## Self-Review
Cada ato encerra com Protocol G + relatório. (regra casa: self-review POR ATO, não no fim)

## Artefatos
Plano completo por ato em `docs/plans/v4.5-relay-halls/`:
- `ato1-envelope.md` (mailbox/relay: envelope + quarentena) — PASS
- `ato2-reativo.md` (watcher: heartbeat 3 estados + loop) — PASS
- `ato3-mcp.md` (server: peers dinâmicos + identity) — PASS
- `ato4-halls.md` (halls.py + tools MCP) — PASS
- `ato5-observatory.md` (visual agents + halls + mailboxes) — PASS
- `ato6-docs.md` (docs + bump + rastro) — PASS

## MODO (a) — detalhar tudo, depois executar
Sequência de execução fixa: Ato 1 → Ato 2 → Ato 3 → Ato 4 → Ato 5 → Ato 6.
Cada ato = TDD (test file), ruff check --fix, pytest um arquivo por vez, commit, self-review.