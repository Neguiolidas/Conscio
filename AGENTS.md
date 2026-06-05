# Conscio Project — Agent Boot Instructions

## On Session Start
1. `cd /home/ubuntu/clawd/Repos/Conscio`
2. Run `python3 -m pytest tests/ -q --tb=no` — expect **347 passing**
3. Read `~/mempalace/diary/_session_handoff.md` for last session context
4. Load skill: `skill_view(name='conscio')`

## Architecture
- 17 modules: 8 core (engine, meta, goals, world, reflect, autoevo, output, tokens) + 6 SQLite (ContentStore, EventBus, TokenTracker, WorldModel, Migrator, SessionLifecycle) + 3 utility (ContextManager, ModelRegistry, SessionRAG)
- DB: `~/.hermes/consciousness/conscio.db` (SQLite WAL)
- Version: 0.2.3

## Key APIs (signatures)

### Core
- `ConsciousnessEngine(model_name, storage_path)` → engine
- `ConsciousnessEngine.close()` — idempotent, flushes WAL
- `ConsciousnessEngine` supports `with` statement (context manager)
- `engine.reflect(world_state, confidence, anomalies)` → dict
- `engine.get_state_for_injection()` → dict
- `engine.record_session_lifecycle(event_type, context, engine=None)` → SessionSummary | None

### ContentStore
- `ContentStore.index(label: str, content: str, category: str, ...)` → int (content_id) — NOTE: first arg is `label`, NOT `source`
- `ContentStore.search(query: str, limit: int)` → list[SearchResult]
- `ContentStore.stats()` → dict (keys: source_count, chunk_count, categories, db_path, db_size_kb)

### EventBus
- `EventBus.emit(event_type: str, category: str, data: dict)` → int (event_id) — NOTE: returns int, NOT Event object
- `EventBus.query(type=None, category=None, limit=20, include_duplicates=False)` → list[Event]
- VALID_TYPES: system, trading, consciousness, external, session, error
- VALID_CATEGORIES: system, trading, consciousness, external, session, reflection, perception, error

### SessionLifecycle
- `record_session_lifecycle(event_type, context, engine=None)` → SessionSummary | None
- `SessionSummary` — dataclass with intents, actions, reasoning, topics, world_model_entities, active_goals, meta_confidence, stale_entities
- `format_heartbeat(summary)` → str (<1.5KB)
- `format_handoff(summary)` → str (richer version)
- `enrich_with_conscio(summary, engine)` → SessionSummary (best-effort, graceful on error)
- `get_latest_session(db_path)` → dict | None (skips cron sessions)

### TokenTracker
- `TokenTracker.record(source: str, chars_in: int, chars_out: int)` → dict
- VALID_SOURCES includes "injection"

## OutputFilter Config Keys
- `{ "max_lines": 200 }` — NOT `{"max": 200}`
- `{ "max_width": 8000 }` — NOT `{"max_chars": 8000}`

## Git
- Last commit: `1ae8a12` — v0.2.3 session lifecycle integration
- Always commit after changes: `git add -A && git commit -m "..."`

## Pitfalls
- MetaCognition and AutoEvolution use JSON files, NOT SQLite — don't call .close() on them
- ContentStore.index() takes `label` as first positional arg, not `source`
- ContentStore.stats() returns `source_count`, NOT `total_documents`
- EventBus uses `query()` method, not `events()`
- EventBus.emit() returns int (event_id), NOT Event object
- TokenTracker `VALID_SOURCES` includes "injection" — valid source
- EventBus VALID_TYPES includes "session" and "error" (added in v0.2.3)
- ContentStore VALID_CATEGORIES includes "session" (added in v0.2.3)
- SessionLifecycle filters out cron sessions (source != 'cron')
- SessionLifecycle strips compaction artifacts and previous heartbeat injections
- enrich_with_conscio() is best-effort — graceful fallback if engine methods fail
