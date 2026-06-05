# Conscio Project — Agent Boot Instructions

## On Session Start
1. `cd /home/ubuntu/clawd/Repos/Conscio`
2. Run `python3 -m pytest tests/ -q --tb=no` — expect 316 passing
3. Read `~/mempalace/diary/_session_handoff.md` for last session context
4. Load skill: `skill_view(name='conscio')`

## Architecture
- 16 modules: 8 core (engine, meta, goals, world, reflect, autoevo, output, tokens) + 5 SQLite (ContentStore, EventBus, TokenTracker, WorldModel, Migrator) + 3 utility
- DB: `~/.hermes/consciousness/conscio.db` (SQLite WAL)
- Version: 0.2.1

## Key APIs (signatures)
- `ContentStore.index(content: str, label: str, source: str)` → int (content_id)
- `ContentStore.search(query: str, limit: int)` → list[dict]
- `EventBus.emit(event_type: str, data: dict)` → int (event_id)
- `EventBus.query(event_type: str, limit: int)` → list[dict]
- `TokenTracker.record(source: str, chars_in: int, chars_out: int)` → dict
- `ConsciousnessEngine.close()` — idempotent, flushes WAL
- `ConsciousnessEngine` supports `with` statement (context manager)

## OutputFilter Config Keys
- `{ "max_lines": 200 }` — NOT `{"max": 200}`
- `{ "max_width": 8000 }` — NOT `{"max_chars": 8000}`

## Git
- Last commit: `a1b7fe3` — v0.2.1 audit bugfixes
- Always commit after changes: `git add -A && git commit -m "..."`

## Pitfalls
- MetaCognition and AutoEvolution use JSON files, NOT SQLite — don't call .close() on them
- ContentStore.index() takes `label` as first positional arg, not `source`
- EventBus uses `query()` method, not `events()`
- TokenTracker `VALID_SOURCES` includes "injection" — valid source
