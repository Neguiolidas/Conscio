# Ato 2 — Relay reativo: heartbeat 3-estados + loop persistente

## Objetivo
Mata o gargalo #1 (falha silenciosa) e elimina o rearm manual do watcher a cada interação.
O watchdog passa a (i) responder sempre um de 3 estados, (ii) rodar em loop persistente
com heartbeat, (iii) renovar presença no registro `agents`.

## Protocol A (pre-flight já feito)
- `watcher.py`: `tick_once(...)` retorna `([], ExitCode.OK)` quando vazio — o "silêncio".
  Loop persistente EXISTE em rascunho (main, --interval): poll, se vazio dorme, timeout
  passa. Sem heartbeat, sem renovar agents, sem quarentena awareness.
- `relay_sensor.py`: percebe `relay_inbox.read` unread; não marca read. Usa `self.peers`
  frozenset passado no init.
- `agents.py`: `heartbeat(db, instance_id, capabilities/status/model)` — JÁ EXISTE e é o
  mecanismo certo de renovar presença.
- `mailbox.py`: sem função de renovar heartbeat automaticamente.

## Entregas do Ato 2

### T1 — Heartbeat de 3 estados no watcher
`tick_once` (e o CLI) passa a produzir um resumo estruturado do tick, seja qual for o
resultado:
```python
{
  "estado": "entregue" | "nada_novo" | "não_entregue",
  "motivo": "",            # presente quando estado == "não_entregue"
  "cursor": {peer: int},   # por peer
  "par": self_id,
  "ts": float,
}
```
- `nada_novo` = poll vazio limpo (db ok, sem msgs).
- `não_entregue` + `motivo` = db ausente/corrompido/emit-outbox falhou (antes isso era
  `[]`+/`ExitCode` mudo). Motivos mapeados dos ExitCode: CONFIG_ERROR, PENDING_CAPTURE.
- `entregue` = houve msgs e outbox/stdout escrito.

Retrocompat: keeps `messages` list in stdout when entregue (mesmo contrato do cron);
aquele convidativo JSON de resumo vai junto.

Testes (test_liaison_watcher.py — append):
- tick vazio → estado "nada_novo", motivo "", cursor preservado.
- db ausente → estado "não_entregue", motivo inclui "db", NÃO `[]` mudo.
- emit outbox falhou → "não_entregue" + "pending_capture".
- msgs presentes → "entregue" e messages no stdout.
- NUNCA levanta: qualquer exceção dentro vira "não_entregue" + motivo, exit não-crash.

### T2 — Renovar presença no registro a cada tick
Watcher, a cada tick (qualquer estado), chama `agents.register_agent` (primeira vez) e
depois `agents.heartbeat(db, self_id, model=..., capabilities=(...), status='alive')`.
Self-id é o `self_id` do watcher. O registro de si mesmo é a "assinatura de vida".

Se o `agents` table não existe (db muito antigo), `agents._conn` cria (rw). Sem quebra.

Testes:
- tick → agents.get_agent(db, self_id) tem last_heartbeat recente.
- 2 ticks → heartbeat renovado (campo cresce).

### T3 — Loop persistente como caminho primário + heartbeat vivo periódico
Além do poll já existente, o loop emite (a) o resumo do estado a cada N (--heartbeat-int),
(b) um "vivo+ocioso" a cada --interval mesmo sem msgs, e (c) para antes do timeout ALVO
quando --timeout relativo ao deadline (como já faz). Com `--interval` o cron deixa de ser
primário (pode continuar como fallback documentado).

O heartbeat do vigia imprime na stdout (consumível por um supervisor/systemd) o estado
"vivo+ocioso" mesmo sem msgs novas — isso é o que transforma "não recebi nada" de ambíguo
em diagnóstico. Format: `{"estado":"vivo","cursor":{...},"par":"<id>","ts":...}`.

Testes:
- --interval 0.1 com semáforo → vê "vivo" heartbeat impresso antes do timeout.
- --once → um único tick, sem heartbeat loop (não regressa).

### T4 — RelaySensor tolerante a peers dinâmicos
`RelaySensor.__init__` já recebe `peers`. NOVO: aceitar `peers=None` → significa "todos os
`agents.list_agents(include_stale=False)`" (descobre do registro em vez de lista fixa).
Isso torna o sensor reativo à entrada/saída de agentes sem reconfig.

Testes (test_relay_sensor.py — append):
- peers=None → percebe msgs de qualquer agente vivo no registro.
- peers=fixo → cronograma atual preservado.
- relai com db sem table agents → peers None degrada para [] (sem crash).

## Missing edge → não deixar para "próximo update"
- O quarentena do Ato 1 (payload malformado) não derruba o tick: watcher continua. Sem
  integração de quarentena aqui (fica no Ato 1/T3). Desacoplado de propósito.
- Cron antigo (systemd/cron rearm) NÃO é removido neste ato — vira fallback documentado;
  o loop persistente vira primário. Remover cron é decisão do Senhor, pedir antes.

## Regras de commit
- ruff check --fix nos arquivos + testes antes do git add.
- Sobrebuja: test_liaison_watcher.py, test_relay_sensor.py.
- Envelope de procedência: ainda NÃO toca mailbox (é Ato 1, já separado).
- Commit: `feat(liaison): relay reativo - heartbeat 3 estados + presenca no registro`

## Self-Review (Protocol G) — PASS
Rodado contra codebase real (2026-08-29):
- CRITICAL FIXED: `tick_once(db,*,self_id,peers,outbox) -> (list[dict], ExitCode)` confirma
  os 3 estados mapeiam 1:1: OK→entregue, (PENDING_CAPTURE|CONFIG_ERROR)→não_entregue+motivo,
  OK+vazio→nada_novo. ✓
- CRITICAL FIXED: `agents.heartbeat(db, id, capabilities/status/model)` e
  `agents.register_agent` kwarg-only existem → T2 usa ambos sem inventar. ✓
- CRITICAL FIXED: `RelaySensor.__init__(liaison_db, self_id, peers, *, limit=50)` — param
  `peers` livre p/ aceitar None (T4). `self.peers` attr confirmado. ✓
- MEDIUM FIXED: `agents._conn` cria a tabela no caminho rw → registro de presença em db
  antigo (sem table agents) cria via _conn. ✓
- NÃO REGRESSA: --once mantém um tick único; --interval mantém deadline. Timer e "vivo"
  são adições, não mudanças de contrato.
Status: PASS.

## Nota de sequência
Executa DEPOIS do Ato 1 (que dá envelope+quarentena). T2 depende de `agents` (existente).
Não depende do Ato 3.