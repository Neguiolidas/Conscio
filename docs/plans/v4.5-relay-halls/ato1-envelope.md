# Ato 1 — Envelope de procedência + quarentena (mailbox/relay)

## Objetivo
Mata o gargalo #4 (identidade no corpo) e #2 (dado ruim derruba fila) do documento.
Todo `mailbox.send` carrega um envelope `_meta.from` com a identidade **do runtime**
(modelo/familia/runtime/papel), e payload malformado vai para `quarantine` em vez de
derrubar a leitura.

## Protocol A (pre-flight já feito)
- `mailbox.py`: `_SCHEMA` tem `messages(id, from_instance, to_instance, type, payload, ts, read_ts)`.
  `send()` declara payload como `dict`, insere `json.dumps(payload)`. `inbox()` faz
  `json.loads` e `continue` em row malformado (perda silenciosa).
- `relay.py`: `validate_send(to, type, payload, peers)` — boom em peer desconhecido.
  `is_relay_message(row, peers)` — filtro de allowlist.
- `agents.py`: row `(instance_id, model, status, capabilities, last_heartbeat)`. SEM
  colunas `familia/runtime/papel/nome` ainda.
- Tests que vão quebrar: `test_liaison_mailbox.py`, `test_liaison_relay.py`.

## Entregas do Ato 1

### T1 — Colunas de identidade no `agents` table
Adicionar a `familia`, `runtime`, `papel`, `nome` ao `_DDL` e ao `register_agent` /
`heartbeat` / `get_agent` / `list_agents`. Migração segura: `ALTER TABLE ... ADD COLUMN`
envolto em try/sqlite3 (coluna já existe → ignora). Colunas com DEFAULT '' / 'alive'.

Migração (inside `agents._conn`, non-read_only):
```sql
ALTER TABLE agents ADD COLUMN familia TEXT NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN runtime TEXT NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN papel   TEXT NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN nome    TEXT NOT NULL DEFAULT '';
```
usando um helper `_ensure_identity_columns(conn)` que checa `PRAGMA table_info` e só roda
o ALTER para colunas ausentes.

`register_agent(..., nome="", familia="", runtime="", papel="")` — novos kwargs default ''
(compat slog: callers sem eles continuam válidos).

Testes (test_liaison_agents.py — appender ao arquivo existente):
- register com nome/familia/runtime/papel → get_agent retorna preservados.
- register sem esses kwargs → defaults vazios (não quebra callers antigos).
- migração idempotente: chamar _ensure_identity_columns 2x não estoura.
- db antigo (criado sem as colunas) → após _conn, get_agent retorna com colunas novas.

### T2 — Envelope `_meta.from` no `mailbox.send`
`send()` ganha `identity: dict | None = None`. Se for dict não-vazio, grava
`payload["_meta"] = {"from": identity, "id": <lastrowid após insert>}`. O `id` precisa do
row após insert → fazer insert, pegar `lastrowid`, e se identity, dar `UPDATE messages SET
payload=? WHERE id=lastrowid` com o payload enriquecido. Registra o ts real.

Assinatura preserva callers existentes: `identity=None` → comportamento idêntico ao atual
(sem `_meta`).

Testes (test_liaison_mailbox.py):
- send(identity={...}) → inbox mostra payload["_meta"]["from"] == identity.
- send sem identity → sem _meta (não regressa).
- identity com valor override não sobrescreve _meta já presente? — decide: _meta é ordem,
  se payload já tinha _meta, o envelope do runtime vence (sobrescreve) — nunca deixar
  identidade do corpo prevalecer sobre a do runtime (é o ponto do doc).

### T3 — Quarentena de payload malformado
Nova tabela `quarantine` no `_SCHEMA`:
```sql
CREATE TABLE IF NOT EXISTS quarantine (
    id          INTEGER PRIMARY KEY,
    source_row  INTEGER NOT NULL,
    motivo      TEXT NOT NULL,
    payload_raw TEXT,
    ts          REAL NOT NULL
);
```
`mailbox.inbox()` / `thread()` em vez de `continue` em JSON inválido: chama
`quarantine(db, source_row_id, motivo, payload_raw)` e segue. `list_quarantine(db, limit)`
retorna os rows. `purge_quarantine(db, older_than_days)` limpa.

Importante: `inbox` lê de um connection read-only-ish hoje? Não — `_connect` abre
read-write (`mode=ro` não é usado em inbox actual). Então escrever a quarentena na mesma
conexão é OK. Mas tread() também. Cuidado: `liaison_view.py` abre mode=ro e NÃO deve
escrever quarentena (projeção) — deixa como está (viewer não quarantina; é leitura pura).

Testes (test_liaison_mailbox.py + test_liaison_relay.py):
- inbox com 1 row válido + 1 inválido → retorna o válido; quarantine tem 1 row com motivo.
- thread com row inválido não estoura; row vai pra quarantine; resto entrega.
- list_quarantine/purge_quarantine funcionam.
- db quebrado continua `[]` (não regressa o degrade).

### T4 — `relay.validate_send` aceita peers dinâmicos
`validate_send(to=..., peers=list_agents_ativos)` continua mesma assinatura; a MUDE é no
caller (server) que resolve peers de `agents` — não aqui. Mas: is_relay_message passa a
aceitar row que tenha `_meta.from` válido mesmo se `from_instance` não estiver na allowlist
quando `allow_unregistered=True`. Isso é o "primeiro contato de peer novo entra".

Na prática: relay.py ganha função `envelope_of(row) -> dict | None` que devolve
`payload._meta.from` se existir, senão None. Não muda validate_send (isso é Ato 3).

Testes (test_liaison_relay.py):
- envelope_of(row com _meta) → o dict.
- envelope_of(row sem _meta) → None.
- envelope_of com _meta[:from] malformado (não-dict) → None (defensivo).

## Missing edge → não deixar para "próximo update"
- Payload cujo `_meta.from` existe mas não é dict: quarentena? NONO. _meta.from não-dict é
  só ignorado (None) — envelope é metadado, não bloqueia entrega. Quarentena é só para
  payload raiz que não parseia.

## Regras de commit
- ruff check --fix nos 4 arquivos + 3 de teste antes do git add.
- Um teste de cada vez: `python -m pytest tests/test_liaison_mailbox.py -q` (um processo).
- Commit: `feat(liaison): envelope de procedencia + quarentena de payload malformado`

## Self-Review (Protocol G) — PASS
Rodado contra codebase real (2026-08-29):
- CRITICAL FIXED: `inbox()` e `thread()` usam `_connect` (rw, sem mode=ro) → escrita na
  quarentena na mesma conexão é válida. ✓
- CRITICAL FIXED: `register_agent` é kwarg-only → `nome/familia/runtime/papel` como kwargs
  novos com default '' é compatível (não quebra callers). ✓
- CRITICAL FIXED: `mailbox.send(db,*,from,to,type,payload,ts=None)` e `thread(db,a,b,*,limit)`
  batem com os planos T2/T3. ✓
- MEDIUM FIXED: `agents._conn(read_only=True)` NÃO cria/migra → `_ensure_identity_columns`
  roteia para o caminho rw apenas. ✓
- JÁ RATIFICADO v4.1.1: capabilities é CSV string (sem array type). Não corrigir.
- NÃO REGRESSA: inbox/thread com db quebrado continua `[]`; envelope `_meta.from` não-dict
  é ignorado (None), não bloqueia entrega.
Status: PASS.