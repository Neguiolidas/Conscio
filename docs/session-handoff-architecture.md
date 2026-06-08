# 🔄 Arquitetura de Handoff Conscio — Documentação Técnica

> Garante continuidade de contexto entre sessões do **Hermes Agent**, capturando o essencial da sessão antes da perda e re-injetando-o automaticamente na nova sessão.

---

## 1. Visão Geral do Sistema

Quando uma sessão do Hermes Agent expira (por exemplo, às 04:00 BRT), é resetada (`/new`, `/reset`) ou é suspensa, todo o contexto conversacional do modelo é perdido. O sistema de handoff Conscio **captura o essencial** no momento da perda (eventos `session:end` / `session:reset`) e **re-injeta** esse contexto na sessão seguinte (evento `session:start`), sem modificar o código core do gateway.

### Pipeline de Alto Nível

```
session:end / session:reset ──► hook conscio-handoff ───► gera heartbeat + handoff
         │                                                   │
         │                                                   ▼
         │                                            disco (MemPalace)
         │                                                   │
         │                    session:start ◄──────────────┘
         │                          │
         │                          ▼
         │                   hook _inject_heartbeat()
         │                          │
         ▼                          ▼
    [sessão expira]        gateway emit_collect("session:start")
         │                          │
         │                          ▼
         │            strings dos hooks são prependadas ao context_prompt
         │                          │
         ▼                          ▼
    [nova sessão] ◄── heartbeat no contexto do agente
```

---

## 2. Componentes Principais

---

### 2.1 Hook: `conscio-handoff`

**Local:** `~/.hermes/hooks/conscio-handoff/`

O hook registra **3 eventos** em `HOOK.yaml`:
- `session:end` — sessão expirou
- `session:reset` — sessão foi resetada (`/new` ou `/reset`)
- `session:start` — nova sessão iniciada

#### `HOOK.yaml`
```yaml
name: conscio-handoff
events:
  - session:end
  - session:reset
  - session:start
```

O registro via `HOOK.yaml` sobrevive a `git pull`, restart e atualizações do gateway — **zero patch no código core**.

#### `handler.py` (Handler Unificado)

O handler unificado possui **3 caminhos de execução**:

| Evento | Função Destino | Comportamento |
|---|---|---|
| `session:start` | `_inject_heartbeat()` | Lê `_latest_heartbeat.md`, verifica staleness (< 24h), retorna string wrapped em `[HEARTBEAT]` markers |
| `session:end` | `_generate_handoff()` | Chama `record_session_lifecycle()` — captura, enriquece, persiste |
| `session:reset` | `_generate_handoff()` | Idêntico ao `session:end` — captura antes da destruição |

##### `session:start` — Injeção de Heartbeat

```python
def _inject_heartbeat() -> str | None:
    if not HEARTBEAT_PATH.exists():
        return None

    if _is_stale(HEARTBEAT_PATH):  # > 24h
        return None

    content = HEARTBEAT_PATH.read_text(...)

    return (
        "[HEARTBEAT — Contexto da sessão anterior (auto-injetado)]\n"
        f"{content}\n"
        "[FIM DO HEARTBEAT — Use este contexto para continuidade]"
    )
```

Chave: retorna uma `str`. O gateway usa `emit_collect("session:start")` e **prependa** todas as strings não-vazias ao `context_prompt`. O agente recebe o heartbeat automaticamente no contexto.

##### `session:end` / `session:reset` — Geração do Handoff

```python
def _generate_handoff(event_type: str, context: dict) -> None:
    summary = record_session_lifecycle(event_type, context, engine=None)
    # ... log output
```

Retorna `None` (fire-and-forget). O gateway não espera retorno nestes eventos.

---

### 2.2 `session_lifecycle.py` — Módulo Core do Pipeline Conscio

**Local:** `/home/ubuntu/clawd/Repos/Conscio/conscio/session_lifecycle.py`

Função de entrada principal:
```python
record_session_lifecycle(event_type, context, engine=None) -> SessionSummary | None
```

#### Pipeline Completo (extract → enrich → emit → index → reflect → dream → write)

```
record_session_lifecycle()
  │
  ├── 1. EXTRACT ─┬─► get_session_by_id(SESSION_DB, context["session_id"])
  │                │    (PRIMARY — fix de race condition)
  │                │
  │                └─► get_latest_session(SESSION_DB)
  │                     (FALLBACK — usado por cron sem session_id)
  │
  ├── 2. BUILD ──────► SessionSummary (intents, actions, reasoning, topics)
  │
  ├── 3. ENRICH ──────► enrich_with_conscio(summary, engine)
  │                        ├─ world model entities
  │                        ├─ active goals
  │                        ├─ meta confidence
  │                        ├─ trajectory
  │                        ├─ coherence
  │                        ├─ voice preset
  │                        ├─ self-prompt
  │                        └─ dream recommendation
  │
  ├── 4. EMIT ────────► engine.event_bus.emit(type="session", ...)
  │
  ├── 5. INDEX ───────┬─► content_store.index(label="heartbeat_...", content=heartbeat, category="session")
  │                    │
  │                    └─► content_store.index(label="handoff_...", content=handoff, category="session")
  │
  ├── 6. REFLECT ─────► engine.reflect(world_state, recent_events, confidence, anomalies)
  │
  ├── 7. DREAM ───────► engine.dream()  # mitosis + DB consolidation
  │
  └── 8. WRITE DISK ──┬─► ~/mempalace/diary/_latest_heartbeat.md
                      │
                      └─► ~/mempalace/diary/_session_handoff.md
```

#### Bug Crítico Corrigido: Race Condition no `session_id`

**Problema:** No evento `session:end`, o Gateway já rotacionou a sessão no `state.db`. `get_latest_session()` retornaria a nova sessão (quase vazia), e o handoff seria quase inútil.

**Fix:** Usar `context.get("session_id")` como lookup primário via `get_session_by_id()`. O gateway patchado (`feat/session-lifecycle-hooks`, commit `c6f303b09`) emite o `session_id` no contexto do hook.

```python
context_sid = context.get("session_id") if context else None
if context_sid and context_sid != "cron":
    session = get_session_by_id(SESSION_DB, context_sid)  # ← fix principal
else:
    session = get_latest_session(SESSION_DB)              # ← fallback cron
```

#### `SessionSummary` (DataClass)

| Campo | Origem | Conteúdo |
|---|---|---|
| `session_id` | state.db ID | UUID da sessão |
| `model` | state.db | Modelo usado (`glm-5.1`, etc.) |
| `started_at` | state.db | Timestamp ISO de início |
| `message_count` | state.db | Total de mensagens |
| `intents` | Extrair mensagens user | Pedidos do usuário (filtrado: sem noise/cron) |
| `actions` | Extrair mensagens assistant | Primeiras linhas das respostas do agente |
| `reasoning` | Extrair padrões | Trechos com palavras-chave (bug, erro, decisão, motivo) |
| `topics` | Inferência sobre intends+actions | Categorias: trading, conscio, orion, infra, debug, code, hermes |
| `world_model_entities` | Conscio engine | Top 5 entidades do world model |
| `active_goals` | Conscio engine | Top 3 goals ativos |
| `meta_confidence` | Conscio engine | Confiança média da meta-cognition |
| `trajectory` | Conscio engine | Direção cognitiva (`shard → goal`) |
| `coherence` | Conscio engine | Score de coerência (v0.6) |
| `voice` | Conscio engine | Preset de voz |
| `self_prompt` | Conscio engine | Último self-prompt |
| `dream_recommended` | Conscio engine | Badge/marker de dream recomendado |

#### Funções de Formatação

##### `format_heartbeat(summary) → str` (< 1.4KB)

Overwrite diário. Compacto. Contém: sessão ID, modelo, mensagens, título, trajetória, voice, tópicos, confiança Conscio, pedidos do senhor (compactados), ações, metas Conscio, entidades stale.

##### `format_handoff(summary) → str`

Preservado para referência manual. Mais rico: tudo do heartbeat + raciocínio completo, estado Conscio (world model, goals, coherence, self-prompt, dream), e instruções para a próxima sessão.

---

### 2.3 Cron: `hermet-session-handoff`

| Config | Valor |
|---|---|
| **Job ID** | `be5aedafadde` |
| **Schedule** | `0 11 * * *` (08:00 BRT) |
| **Script** | `python3 /home/ubuntu/clawd/Repos/Conscio/scripts/session_handoff.py` |
| **no_agent** | `True` (script-only, sem LLM) |

#### Objetivo: Safety Net

Se o `session:end` hook **não disparou** (gateway crash, SIGKILL, out-of-memory, etc.), o cron captura a sessão expirada nas primeiras horas da manhã.

#### Pipeline na Cron

```python
# session_handoff.py → record_session_lifecycle()
context = {
    "platform": "cron",
    "user_id": "",
    "session_key": "cron:handoff",
    "session_id": "",   # <-- vazio → usa get_latest_session() como fallback
}
```

**Atenção:** Cron usa `get_latest_session()` (fallback). Isso é **menos confiável** que o hook, pois pode pegar a sessão errada se houver uma race condition. Mas é essential como cobertura de falha do gateway.

#### Integração Conscio

O script cron agora utiliza o pipeline Conscio **integrado** (`session_lifecycle.py`). Não é mais uma extração standalone pobre — obtém-mos o mesmo enrichment de world model, EventBus emission, ContentStore indexing, reflection e dream que o hook.

---

### 2.4 Gateway Patch: Emissão de `session:end` / `session:reset`

**Branch:** `feat/session-lifecycle-hooks`  
**Commit:** `c6f303b09` (na branch, ainda não em main)

**Arquivo modificado:** `~/.hermes/hermes-agent/gateway/run.py` (linhas ~5047-5067)

O patch adiciona a **emissão de hooks** quando sessão auto-expira. Sem isto, o gateway jamais dispararia `session:end` — o hook só funcionaria em `/new` e `/reset` explícitos.

**Contexto incluído no hook:**
```python
context = {
    "session_id": current_session_id,
    # ... outros campos de contexto
}
```

Isso permite que `get_session_by_id()` faça o lookup preciso da sessão correta.

#### Alternativa Zero-Patch (versa atual)

Nossa solução atual usa **apenas hooks** — o `session:start` usa `emit_collect()` que já existe no gateway (linhas ~8869-8899, código nativo). Não necessitamos de modificações no core do gateway para a injeção de heartbeat.

---

## 3. Arquivos em Disco

| Arquivo | Tipo | Tamanho | Ciclo de Vida |
|---|---|---|---|
| `~/mempalace/diary/_latest_heartbeat.md` | Compacto | < 1.5KB | Overwrite diário; elimina conteúdo anterior |
| `~/mempalace/diary/_session_handoff.md` | Rica / Debug | ~2-5KB | Preservado; referência manual e debug |

### Estrutura de Diretórios

```
~/
└── mempalace/
    └── diary/
        ├── _latest_heartbeat.md    # <-- injetado automaticamente no contexto
        └── _session_handoff.md     # <-- referência manual e debugging
```

---

## 4. Formato dos Documentos

### `_latest_heartbeat.md` (Compacto)

```markdown
# ♥ Heartbeat — 2025-01-24

**Sessão:** `abc123de...`
**Modelo:** glm-5.1
**Mensagens:** 47
**Título:** Refactor do módulo X
**Trajetória:** SHARD_INTEGRATION → fix gateway handoff
**Voz:** hermet-professional
▷ coherence: 0.87 dominant: EXECUTION
⊙ voice: hermet-professional
❓ self-prompt: Verificar race condition no handoff...
☾ dream: DREAM_CONSOLIDATION
**Tópicos:** conscio, debug, code
**Conscio confiança:** 0.85

---

## 📋 Pedidos do Senhor
1. Documentar a arquitetura do handoff
2. Corrigir lookup por session_id no lifecycle
3. ...

## 🤖 Ações do Hermet
1. Criar arquivo markdown com a documentação
2. ...

## 🎯 Metas Conscio
- Consolidar pipeline de handoff
- ...

---
*Gerado: 13:45 UTC*
```

### `_session_handoff.md` (Rico)

Contém tudo o do heartbeat +:

- Seção **"Raciocínio e Decisões"** (`## 🧠`)
- Seção **"Estado Conscio"** completo:
  - World model entities
  - Active goals
  - Meta confidence
  - Trajectory, vibes, identity anchor
  - Coherence + coherence note
  - Voice preset
  - Self-prompt
  - Dream recommended
- Seção **"Para a Próxima Sessão"** com instruções operacionais

---

## 5. Decisões Arquiteturais

### 5.1 Hook-based > Gateway Patch

- **Hooks sobrevivem** a `git pull`, restart e upgrades.
- Separados do código core; evita conflitos de merge.
- Fallback natural: se o hook não carregar, gateway continua funcionando.

### 5.2 `emit_collect` > `emit`

- `session:start` precisa do **retorno** (string) para injeção no `context_prompt`.
- `emit()` é fire-and-forget; `emit_collect()` coleta retornos dos hooks.
- `session:end` e `session:reset` usam `emit()` (não precisam de retorno).

### 5.3 Stale Check (24h)

- Heartbeat > 24h não é injetado.
- Evita contexto antigo/morado se não houver sessão real recente.
- Se heartbeat não existir → silencioso (no-op).

### 5.4 `[HEARTBEAT]` Markers

- Permite ao agente **identificar** o bloco como contexto de sessão anterior.
- Facilita parsing, debugging, e futura filtragem/separação.

### 5.5 `session_lifecycle.py` como Fonte Única de Verdade

- Tanto **hook** quanto **cron** usam o mesmo `record_session_lifecycle()`.
- Garante consistência de formato, enrichment e behavior.
- Evita duplicação de código extraction/formatting.

### 5.6 Best-Effort Conscio Enrichment

- Se `engine.world.list_entities()` falha, summary continua gerado (sem entities).
- Se `content_store.index()` falha, persistence retorna para disco funcionando.
- Se `engine.reflect()` ou `engine.dream()` falha, heartbeat/handoff já está salvo.

---

## 6. Fluxo de Dados Completo (Diagrama Texto)

```
                  ┌───────────────────────────────────────────────────────────────┐
                  │                  SISTEMA DE HANDOFF CONSCIO                     │
                  └───────────────────────────────────────────────────────────────┘

CENÁRIO: Sessão expira (04:00 BRT) ou usuário digita /new /reset
  │
  ▼
┌───────────────────────────────────┐
│  Gateway (run.py, patchado)      │
│  • Emite session:end /           │
│    session:reset via              │
│    HookRegistry                   │
│  • Inclui session_id no context   │
└───────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────┐
│  Hook: handler.py                 │
│  _generate_handoff()              │
│  chama record_session_lifecycle() │
└───────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────┐
│  session_lifecycle.py             │
│  record_session_lifecycle()       │
│                                   │
│  1. get_session_by_id(session_id) │  ← fix: obtém sessão CORRETA
│     │                             │    (fallback: get_latest_session())
│     │                             │
│  2. build SessionSummary          │
│     (intents, actions, reasoning, │
│      topics)                      │
│     │                             │
│  3. enrich_with_conscio()         │  ← best-effort
│     (world model, goals,          │
│      confidence, trajectory,      │
│      coherence, voice,            │
│      self-prompt, dream)          │
│     │                             │
│  4. EventBus.emit(type="session") │
│     │                             │
│  5. ContentStore.index() x2       │  ← FTS5 searchable
│     │                             │
│  6. engine.reflect()              │
│     │                             │
│  7. engine.dream()                │  ← mitosis + consolidate
│     │                             │
│  8. WRITE DISK:                   │
│     ├─ _latest_heartbeat.md     │  ← compact, overwrite
│     └─ _session_handoff.md        │  ← rico, preservado
└───────────────────────────────────┘
  │
  │  … (sem sessão entre 04:00 e 08:00) …
  │
  ▼
CENÁRIO: Nova sessão começa
  │
  ▼
┌───────────────────────────────────┐
│  Gateway emit_collect("session:start")
│  coleta strings dos hooks ativos  │
└───────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────┐
│  Hook: _inject_heartbeat()        │
│  • Lê _latest_heartbeat.md        │
│  • Verifica stale (< 24h)         │
│  • Retorna string wrapped          │
│    em [HEARTBEAT] markers         │
└───────────────────────────────────┘
  │
  ▼
┌───────────────────────────────────┐
│  Gateway prependa ao context_prompt │
│  (strings não-vazias coletadas    │
│   são inseridas no contexto)      │
└───────────────────────────────────┘
  │
  ▼
[Agente recebe heartbeat no contexto
automaticamente — zero ação humana]
```

---

## 7. Cron como Safety Net

### Motivação

O hook `session:end` depende do gateway estar vivo para emitir o evento. Se o gateway recebe SIGKILL, OOM, ou simplesmente crasha, o evento nunca é disparado.

### Resolução: Cron às 08:00 BRT

| Aspecto | Descrição |
|---|---|
| **Horário** | 08:00 BRT (3 horas após o expiry típico de 04:00) |
| **Objetivo** | Capturar sessões onde hook não funcionou |
| **Limitação** | Usa `get_latest_session()` (lookup por ordem de início), que pode ter race condition se nova sessão já começou |
| **Mitigação** | Job ID `be5aedafadde` — rodar mesmo sem agente (`no_agent=True`) |

### Compatibilidade: `get_latest_session()` vs `get_session_by_id()`

| Caminho | Fonte do session_id | Confiabilidade | Usado por |
|---|---|---|---|
| `get_session_by_id()` | Hook context (`context["session_id"]`) | **Alta** (lookup preciso) | Hook `session:end` / `session:reset` |
| `get_latest_session()` | MAX(started_at) no state.db, `source != 'cron'` | **Média** (vunerável à race) | Cron fallback + ocorrwere sessão anômala |

---

## 8. Troubleshooting

### 8.1 Heartbeat Não É Injetado

**Sintoma:** Nova sessão não mostra `[HEARTBEAT]` no contexto.

**Checklist:**
1. Hook carregou? Verifique logs do gateway: `cat ~/.hermes/logs/gateway.log | grep "hook"` — procure por `N hook(s) loaded`.
2. `_latest_heartbeat.md` existe? `ls -la ~/mempalace/diary/_latest_heartbeat.md`
3. Arquivo está stale? `stat ~/mempalace/diary/_latest_heartbeat.md` → verifique mtime (deve ser < 24h).
4. Gateway disparou `session:start` com `emit_collect()`? Verifique `gateway.log` por `emit_collect`.

### 8.2 Handoff Vazio ou Incompleto

**Sintoma:** `_session_handoff.md` tem 0 bytes ou dados sem sentido.

**Checklist:**
1. `session_id` está no context do hook? `grep "session_id" ~/.hermes/logs/gateway.log`.  
   Se não estiver, o **gateway patch** não está aplicado ou branch não está ativa.
2. A sessão no DB tem mensagens? Verifique `state.db`: `SELECT message_count FROM sessions WHERE id='...'`.
3. Filtro de cron está funcionando? Se `source = 'cron'`, a sessão é ignorada.

### 8.3 Cron Fazendo Handoff Ruim

**Sintoma:** Cron gera handoff de sessão errada.

**Checklist:**
1. Verifique se o gateway realmente crashed (provavelmente a sessão actual não tem hook).
2. Confira `get_latest_session()` — se nova sessão já começou, ele retorna a sessão nova (quase vazia).

**Fix:** O cron é safety net. O fix real é garantir que o hook `session:end` está funcionando. Verifique o gateway patch e logs.

### 8.4 Gateway Patch Perdido

**Sintoma:** Sessões expirando não geram handoff.

**Checklist:**
1. Confirme a branch: `cd ~/.hermes/hermes-agent && git branch` — deve estar em `feat/session-lifecycle-hooks`.
2. Verifique o commit: `git log --oneline -3` — `c6f303b09` deve estar presente.
3. Restaurar patch: `git checkout feat/session-lifecycle-hooks` e restart do gateway.

### 8.5 quantidade de bytes do heartbeat inesperada

- Veja `gateway.log` por erros gerados em `handler.py`.
- Verifique permissões de escrita em `~/mempalace/diary/`.
- Verifique se `state.db` existe e está acessível.

---

## 9. Resumo de Decisões Arquiteturais

| Decisão | Justificativa | Trade-off |
|---|---|---|
| **Hook-based** (vs gateway core patch) | Sobrevive git pull, restart, updates; isolado | Dependência de gateway emitir eventos corretamente |
| `emit_collect` para `session:start` | Coleta retornos para injeção de contexto | Gateways antigos sem `emit_collect` não suportam |
| Overwrite diário do heartbeat | Não acumula — controle de tamanho preditável | Só uma sessão de history armazenada automaticamente |
| 24h staleness check | Evita injeção de contexto antigo e obsoleto | Pode perder contexto se sessão não durar 24h |
| `session_lifecycle.py` unificado | Hook e cron usam o mesmo codepath | Consistência perfeita; mais fácil manter |
| Best-effort Conscio enrichment | Handoff ainda funciona se engine fail | Dados de enrichment podem estar ausentes silenciosamente |
| `[HEARTBEAT]` markers | Visível, parseável, descartável | Adicional de ~60 bytes na string injected |

---

## 10. Dependências

```
Python 3.10+
Hermes Agent (com HookRegistry + emit_collect support)
Conscio >= 0.2.3
  ├── engine (ConsciousnessEngine)
  ├── event_bus (EventBus)
  ├── content_store (ContentStore)
  └── …
state.db (Hermes — sessões e mensagens)
```

---

## Referências

- **Hook:** `~/.hermes/hooks/conscio-handoff/`
- **Core:** `/home/ubuntu/clawd/Repos/Conscio/conscio/session_lifecycle.py`
- **Cron:** `/home/ubuntu/clawd/Repos/Conscio/scripts/session_handoff.py`
- **Gateway patch:** `~/.hermes/hermes-agent/gateway/run.py` (linhas ~5047-5067 e ~8869-8899)
- **MemPalace:** `~/mempalace/diary/`
- **DB:** `~/.hermes/state.db`

---

> **Versão:** 0.2.3  
> **Último commit:** `1ae8a12` — v0.2.3 session lifecycle integration  
> **Autor:** Hermet / Conscio Project  
> **Data:** 2025-06-07