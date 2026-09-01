# Ato 3 — MCP server: relays dinâmicos + identidade + self-register/heartbeat

## Objetivo
Leva o registro de presença e o envelope de procedência até o MCP server (que é como o
agente fala com o relay na prática). Peers passam a vir de `agents.list_agents()` em vez do
`--relay-peer` fixo; o server registra a si mesmo e renova heartbeat a cada tick; o
`mailbox.send` usado pelos tools manda o envelope `_meta.from` com o modelo/runtime/papel.

## Protocol A (pre-flight já feito)
- `server.py` `Bindings.__init__`: tem `self.relay_peers = tuple(relay_peers)` (linha 95) e
  `self.self_instance_id` (91). `--relay-peer` lido no argparse (1048).
- `mailbox.send` (Ato 1/T2) ganha `identity` — o server passa `self._identity()`.
- `agents` module: `register_agent`/`heartbeat` prontos (Ato 1 acrescenta familia/runtime/papel).

## Entregas do Ato 3

### T1 — Identity do runtime no Bindings
`Bindings.__init__` ganha kwargs `identity_model: str = "", identity_familia: str = "",
identity_runtime: str = "", identity_papel: str = ""`. Um metodo `_identity(self) -> dict`
monta o envelope:
```python
def _identity(self) -> dict:
    return {
        "id": self.self_instance_id,
        "modelo": self.identity_model,
        "familia": self.identity_familia,
        "runtime": self.identity_runtime,
        "papel": self.identity_papel,
    }
```
Defaults "" (compat). Se `self_instance_id` vazio, retorna `{}` (sem envelope) — não envia
identidade inventada.

### T2 — Self-register + heartbeat no server
No init ou num método chamado por request, o server:
1. `agents.register_agent(liaison_db, instance_id=self_instance_id, model=ident_model,
   familia=..., runtime=..., papel=..., capabilities=("relay",))`
2. e chama `agents.heartbeat(...)` periodicamente (ou a cada operação relay).

Onde: melhor num método `_ensure_registered(self)` chamado no início de cada tool relay
(_relay_send/_relay_broadcast/_relay_inbox/_relay_read) e num tick do relay_sensor. Deflexivo:
se `self_instance_id` vazio, não faz nada.

### T3 — Peers dinâmicos
`_resolve_peers(self) -> set[str]`: se há agents no registro, `= {a["instance_id"]
for a in agents.list_agents(db, include_stale=False)}` (self excluído). Se registro vazio ou
db sem table, cai no `self.relay_peers` (fallback). Toda tool relay passa a usar
`self._resolve_peers()` em vez de `set(self.relay_peers)`.

`validate_send`/`is_relay_message` continuam recebendo `peers` (resolvido) — assinatura
intacta. A mudança é no caller.

Nota D1: `--relay-peer` vira seed inicial (o agente registra a si + os peers como vistos).
Não é apagado; autoridade vira o registro quando há dados.

### T4 — send com envelope nos tools
`_relay_send` / `_relay_broadcast` passam `identity=self._identity() (ou None)` ao
`mailbox.send`. Isso carrega o `_meta.from` no payload (Ato 1/T2). Quando identity vazio,
comportamento antigo.

Testes (append em test_liaison_bindings.py — existe; NÃO criar test_mcp_relay.py):
- `_identity()` com self_id → dict com modelo/familia/runtime/papel.
- `_identity()` self_id vazio → {}.
- `_resolve_peers()` com agents no registro → só vivos (sem o self).
- `_resolve_peers()` sem registro → fallback relay_peers.
- `_relay_send` com identity → inbox receptor mostra payload["_meta"]["from"]["modelo"].
- `_relay_send` sem identity → sem _meta (compat).

## Missing edge → não deixar para "próximo update"
- Self_instance_id vazio: tools relay simplesmente não registram/enviam envelope (fail
  soft), continuam funcionando sem relay. Não quebrar sessão sem relay configurado.
- Concorrência no register (2 requests) → upsert idempotente (agents já cobre).
- db sem table agents no momento do register_agent → `_conn` cria (rw) sem crash.

## Regras de commit
- ruff check --fix server.py + teste bilateral.
- Um arquivo de teste por vez: `python -m pytest tests/test_liaison_bindings.py -q`.
- Envelope só com `mailbox.send` do Ato 1 — não inventar chamada paralela.
- Commit: `feat(mcp): relay com peers dinamicos do registro + envelope de identidade`

## Self-Review (Protocol G) — PASS
Rodado contra codebase real (2026-08-29):
- CRITICAL FIXED: `_relay_send`/`_relay_broadcast` são métodos de `Bindings`; `validate_send`
  chama `self.relay_peers` — alvo exato dos patches T3/T4. ✓
- CRITICAL FIXED: `test_liaison_bindings.py` EXISTE (não `test_mcp_relay.py`) — plano
  corrigido para appender lá. ✓
- CRITICAL FIXED: `Bindings.__init__` tem `self.relay_peers` (95) e `self.self_instance_id`
  (91) — novos kwargs identity entram sem colidir. ✓
- MEDIUM FIXED: `agents._conn` cria table no rw → `_ensure_registered` em db antigo OK. ✓
- NÃO REGRESSA: self_instance_id vazio → fail soft (não registra/envia envelope), relay sem
  config continua funcionando como antes.
Status: PASS.

## Nota de sequência
Depende do Ato 1 (envelope no mailbox) e do Ato 2 (registro). Roda depois de ambos.