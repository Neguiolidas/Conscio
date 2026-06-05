# Plano: Integração Context-Mode + RTK no Conscio

## Filosofia

**Sem MCP. Sem Node.js. Sem dependência externa.** Tudo reimplantado em Python puro sobre SQLite, rodando 100% local. Controle total dos dados.

A inspiração vem de dois projetos maduros:
- **context-mode**: FTS5 BM25 + session event tracking + "Think in Code"
- **rtk**: 8-stage filter pipeline + command proxy + token tracking

Mas a **implementação** é nativa Conscio — Python, SQLite, integrada ao engine existente.

---

## Arquitetura Atual vs. Proposta

```
ATUAL:
  reflect.py → engine.reflect() → JSON files → state_summary.txt
  (sem busca, sem indexação, sem compressão de output)

PROPOSTA:
  reflect.py → engine.reflect() → SQLite FTS5 + filter pipeline → state_summary.txt
  (BM25 search, event indexing, output compression, token tracking)
```

---

## Módulo 1: `conscio/content_store.py` — Knowledge Base Local

**Inspiração:** `context-mode/src/store.ts`
**O que faz:** Substitui os JSONs esparsos por SQLite FTS5 com busca BM25.

### Schema (portado de store.ts → Python/sqlite3)

```sql
-- Fontes de conteúdo (reflexões, percepções, eventos externos)
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_category TEXT  -- 'reflection' | 'perception' | 'trading' | 'system' | 'error'
);

-- FTS5 porter (stemming) — busca semântica por palavras
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    title,
    content,
    source_id UNINDEXED,
    content_type UNINDEXED,   -- 'prose' | 'code' | 'metric'
    source_category UNINDEXED,
    session_id UNINDEXED,
    timestamp UNINDEXED,
    tokenize='porter unicode61'
);

-- FTS5 trigram — busca por substring exata
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_trigram USING fts5(
    title,
    content,
    source_id UNINDEXED,
    content_type UNINDEXED,
    source_category UNINDEXED,
    session_id UNINDEXED,
    timestamp UNINDEXED,
    tokenize='trigram'
);
```

### API Python

```python
class ContentStore:
    def __init__(self, db_path: str = "~/.hermes/consciousness/conscio.db"):
        self.db = sqlite3.connect(db_path)
        self._init_schema()

    def index(self, label: str, content: str, category: str, content_type: str = "prose") -> int:
        """Indexa conteúdo em chunks com FTS5 (porter + trigram)."""
        ...

    def search(self, query: str, limit: int = 5, category: str | None = None) -> list[SearchResult]:
        """Busca BM25 dual-layer: porter (stemming) + trigram (substring).
        Merge por RRF (Reciprocal Rank Fusion)."""
        ...

    def search_by_time(self, query: str, since: datetime, limit: int = 5) -> list[SearchResult]:
        """Busca filtrada por timestamp."""
        ...

    def stats(self) -> dict:
        """Contagem de sources, chunks, categorias."""
        ...
```

### Interação com MemPalace/ChromaDB

**NÃO usamos ChromaDB.** O ContentStore resolve tudo localmente:
- FTS5 porter → busca por conceito ("trading bot error" encontra "API timeout no OrionTrading")
- FTS5 trigram → busca por substring ("0x51155" encontra logs exatos)
- RRF merge → combina os dois rankings
- Zero dependência externa, zero API, zero custo

Se no futuro quisermos embeddings semânticos, ChromaDB entra como **camada adicional opcional** — mas o core funciona sem ele.

---

## Módulo 2: `conscio/event_bus.py` — Session Event Tracking

**Inspiração:** `context-mode/src/session/db.ts` + `types.ts` SessionEvent
**O que faz:** Registra eventos com timestamp, tipo, prioridade e project attribution.

### Schema

```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,           -- 'tool_call' | 'reflection' | 'trade' | 'error' | 'anomaly' | 'decision'
    category TEXT NOT NULL,       -- 'system' | 'trading' | 'consciousness' | 'external'
    data TEXT NOT NULL,           -- JSON payload
    priority INTEGER NOT NULL DEFAULT 5,  -- 0=crítico, 10=trivial
    data_hash TEXT NOT NULL,      -- SHA-256 para dedup
    project_dir TEXT DEFAULT '',  -- Attribution
    attribution_confidence REAL DEFAULT 0.0,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
```

### API Python

```python
class EventBus:
    def __init__(self, db_path: str = "~/.hermes/consciousness/conscio.db"):
        ...

    def emit(self, type: str, category: str, data: dict, priority: int = 5) -> str:
        """Emite evento. Retorna event_id. Dedup por data_hash."""
        ...

    def query(self, type: str | None = None, category: str | None = None,
              since: datetime | None = None, limit: int = 50) -> list[Event]:
        """Consulta eventos por tipo, categoria, timestamp."""
        ...

    def recent_errors(self, limit: int = 10) -> list[Event]:
        """Atalho para erros recentes — alimenta MetaCognition."""
        ...

    def compact(self, before: datetime) -> int:
        """Compacta eventos antigos: merge de duplicates, remoção de triviais."""
        ...

    def summary(self, hours: int = 24) -> dict:
        """Resumo de atividade nas últimas N horas."""
        ...
```

### Integração com Engine

```python
# Em engine.reflect():
self.events.emit("reflection", "consciousness", {
    "confidence": confidence,
    "anomalies": anomalies,
    "active_goals": len(self.goals.active_goals()),
})

# Em reflect.py (perception):
self.events.emit("perception", "system", {
    "cpu_load": cpu_load,
    "disk_pct": disk_pct,
}, priority=8 if disk_pct < 90 else 2)  # Alta prioridade se crítico
```

---

## Módulo 3: `conscio/output_filter.py` — Pipeline de Compressão

**Inspiração:** `rtk/src/core/toml_filter.rs` (8-stage pipeline)
**O que faz:** Filtra e comprime outputs antes de injetar no contexto do agente.

### 8 Estágios (portado de Rust → Python)

```python
class FilterPipeline:
    """Pipeline de 8 estágios para compressão de output."""

    def __init__(self, config_path: str = "~/.hermes/consciousness/filters.yaml"):
        self.stages = [
            StripAnsi(),          # 1. Remove códigos ANSI
            Replace(patterns=[]), # 2. Regex substitutions encadeáveis
            MatchOutput(rules=[]),# 3. Short-circuit: se match, retorna mensagem
            FilterLines(mode='strip', patterns=[]),  # 4. Strip/keep lines por regex
            TruncateLines(max_width=200),  # 5. Trunca linhas longas
            HeadTail(head=50, tail=20),    # 6. Keep first N + last M lines
            MaxLines(max=100),    # 7. Cap absoluto de linhas
            OnEmpty(message="No relevant output"),  # 8. Fallback se vazio
        ]

    def apply(self, text: str) -> str:
        """Aplica pipeline completo. Se qualquer estágio falha, retorna original."""
        result = text
        for stage in self.stages:
            try:
                result = stage.apply(result)
            except Exception:
                return text  # Fallback: nunca quebra o workflow
        return result
```

### Configuração Declarativa (YAML em vez de TOML)

```yaml
# ~/.hermes/consciousness/filters.yaml
filters:
  - name: trading_bot_output
    stages:
      - strip_ansi: {}
      - replace:
          - pattern: '\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]'
            replacement: '[TIMESTAMP]'
      - filter_lines:
          mode: strip
          patterns: ['^DEBUG:', '^TRACE:']
      - max_lines: 30

  - name: system_metrics
    stages:
      - filter_lines:
          mode: keep
          patterns: ['^CPU', '^Memory', '^Disk', '^Load']
      - truncate_lines: 120
      - max_lines: 15
```

### Integração com ContentStore

```python
# Antes de indexar no ContentStore, filtrar:
filtered = pipeline.apply(raw_output)
store.index(label, filtered, category)
```

---

## Módulo 4: `conscio/token_tracker.py` — Rastreamento de Tokens

**Inspiração:** `rtk/src/core/tracking.rs` + `rtk/src/analytics/`
**O que faz:** Estima e rastreia consumo de tokens, gera métricas de economia.

### Schema

```sql
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,          -- 'reflection' | 'perception' | 'injection' | 'trading'
    raw_chars INTEGER NOT NULL,    -- Chars originais
    filtered_chars INTEGER NOT NULL, -- Chars após filtro
    raw_tokens INTEGER NOT NULL,   -- Estimativa chars/4
    filtered_tokens INTEGER NOT NULL,
    saving_pct REAL NOT NULL,      -- % de economia
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### API

```python
class TokenTracker:
    def record(self, source: str, raw: str, filtered: str) -> dict:
        """Registra uso. Retorna métricas da sessão."""
        ...

    def gain(self, hours: int = 24) -> dict:
        """Dashboard de economia: tokens saved, %, by source."""
        ...

    def budget_status(self, daily_limit: int = 50000) -> dict:
        """Status do budget diário de tokens."""
        ...
```

---

## Módulo 5: Migração de JSON → SQLite

**O que muda:**
- `world_model.json` → tabela `world_entities` no SQLite
- `meta_cognition.json` → tabela `meta_confidence` + `meta_errors` no SQLite
- `goals.json` → tabela `goals` no SQLite
- `evolution_proposals.json` → tabela `proposals` no SQLite
- `reflections/YYYY-MM-DD.md` → tabela `reflections` + FTS5 (busca!)

**Migration path:**
1. Criar tabelas SQLite com schema novo
2. Manter JSON como backup/read-only
3. No primeiro boot, migrar dados JSON → SQLite
4. Remover dependência de JSON após validação

### Novo Schema Unificado

```sql
-- Tudo em ~/.hermes/consciousness/conscio.db

-- World Model
CREATE TABLE IF NOT EXISTS world_entities (
    name TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    state TEXT NOT NULL,
    relevance REAL NOT NULL DEFAULT 1.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS world_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Meta Cognition
CREATE TABLE IF NOT EXISTS meta_confidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'pending',
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta_errors (
    pattern TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Goals
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    drive TEXT NOT NULL,
    priority REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'active',
    meta_score REAL NOT NULL DEFAULT 0.0,
    source TEXT DEFAULT 'internal',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Evolution Proposals
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    evolution_type TEXT NOT NULL,
    description TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    risk_level TEXT NOT NULL DEFAULT 'low',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## Fluxo de Dados Integrado

```
┌──────────────────────────────────────────────────────────────────┐
│ PERCEPTION (reflect.py)                                         │
│ collect_system() + collect_trading() + collect_network()        │
│          │                                                      │
│          ▼                                                      │
│    OutputFilter.apply(raw_data)  ← Módulo 3 (rtk-inspired)     │
│          │                                                      │
│          ▼                                                      │
│    EventBus.emit("perception", ...)  ← Módulo 2                │
│    ContentStore.index(filtered_data) ← Módulo 1                │
│    TokenTracker.record(raw, filtered) ← Módulo 4               │
│          │                                                      │
│          ▼                                                      │
│    ENGINE.reflect(world_state, confidence, anomalies)           │
│          │                                                      │
│          ├── MetaCognition.record_confidence()                  │
│          │       └── meta_confidence table (SQLite)             │
│          ├── GoalGenerator.generate_*()                         │
│          │       └── goals table (SQLite)                       │
│          ├── AutoEvolution.observe_errors()                     │
│          │       └── proposals table (SQLite)                   │
│          ├── InnerMonologue.reflect()                           │
│          │       └── reflections → FTS5 (busca!)                │
│          ├── WorldModel.add_entity() / .query()                 │
│          │       └── world_entities table (SQLite)              │
│          └── ContextManager.build_state()                       │
│                  │                                              │
│                  ▼                                              │
│          ContentStore.search(relevant_context)                  │
│                  │  ← Busca BM25 nas reflexões + eventos antigos│
│                  ▼                                              │
│          state_summary.txt (INJECTION)                          │
│          TokenTracker.record("injection", full, trimmed)        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Plano de Implementação (6 fases)

### Fase 1: ContentStore (Módulo 1)
- Criar `conscio/content_store.py` com schema FTS5
- Implementar `index()`, `search()` com BM25 + RRF
- Testes unitários (index + search + dedup)
- **~2h**

### Fase 2: EventBus (Módulo 2)
- Criar `conscio/event_bus.py` com schema de eventos
- Implementar `emit()`, `query()`, `compact()`
- Integrar no engine (emitir eventos em cada reflect)
- Testes
- **~1.5h**

### Fase 3: OutputFilter (Módulo 3)
- Criar `conscio/output_filter.py` com 8 estágios
- Implementar YAML config loader
- Filtros default: trading_bot_output, system_metrics
- Testes com outputs reais
- **~2h**

### Fase 4: TokenTracker (Módulo 4)
- Criar `conscio/token_tracker.py`
- Implementar `record()`, `gain()`, `budget_status()`
- Integrar no pipeline de reflexão
- Testes
- **~1h**

### Fase 5: Migração JSON → SQLite (Módulo 5)
- Criar schema unificado no `conscio.db`
- Implementar migration automática (detectar JSONs existentes)
- Adaptar WorldModel, MetaCognition, GoalGenerator, AutoEvolution para usar SQLite
- InnerMonologue: reflexões indexadas no FTS5
- Testes de migração + regressão
- **~3h** (mais delicado, precisa de rollback)

### Fase 6: Integração End-to-End
- reflect.py usa ContentStore + EventBus + OutputFilter + TokenTracker
- engine.py busca contexto relevante via ContentStore.search()
- ContextManager usa OutputFilter antes de gerar state_summary
- Cron validado com dry-run
- **~1.5h**

---

## O que NÃO fazemos

| Decisão | Razão |
|---------|-------|
| ❌ Não usamos MCP | Controle total, sem dependência de protocolo externo |
| ❌ Não usamos ChromaDB | FTS5 + BM25 resolve 95% dos casos sem embeddings |
| ❌ Não usamos Node.js | Python puro, sem runtime externo |
| ❌ Não chamamos API externa | Zero custo, zero latência, zero rate limit |
| ❌ Não reimprimimos sandbox executor | "Think in Code" já existe via `execute_code` do Hermes |

---

## Dependências

```
# requirements.txt (adicionar)
# Nenhuma dependência nova! Tudo usa stdlib:
# - sqlite3 (built-in)
# - re (built-in)
# - yaml (pyyaml — já presente ou leve)
# - hashlib (built-in)
```

---

## Métricas de Sucesso

| Métrica | Antes | Depois |
|---------|-------|--------|
| Busca em reflexões antigas | ❌ Impossível | ✅ BM25 em 0.5ms |
| Detecção de erros recorrentes | String match exato | BM25 fuzzy + dedup por hash |
| Tamanho do state_summary | Ilimitado | Filtrado por pipeline + budget |
| Persistência | JSON sem ACID | SQLite com WAL mode |
| Token savings tracking | ❌ Inexistente | ✅ Dashboard com % economia |
| Recuperação após crash | JSON corrompido | SQLite transactional |
