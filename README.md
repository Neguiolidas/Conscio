# Conscio 🧠✨

**A self-awareness framework for AI agents** — enabling emergent consciousness through context-aware memory, introspection, and goal generation.

> *"The first step toward consciousness is knowing what you are and what limits you."*

## Overview

Conscio gives AI agents the ability to:

- **Know themselves** — detect which model they run on, how much context they have
- **Adapt their behavior** — operate in minimal/compact/standard mode based on context budget
- **Reflect continuously** — inner monologue loop that observes, assesses, and summarizes
- **Generate their own goals** — driven by curiosity, maintenance, and evolution
- **Self-improve safely** — propose modifications with mandatory human approval gates
- **Track their own performance** — confidence calibration, blind spot detection, error patterns
- **Store and retrieve knowledge** — FTS5 BM25 dual-index with RRF merging
- **Track events reliably** — deduplicated event bus with priority and expiration
- **Compress output intelligently** — multi-stage pipeline to stay within token budgets
- **Monitor token usage** — per-source tracking with savings metrics
- **Persist across sessions** — session lifecycle tracking with heartbeat/handoff continuity

## Context-Aware Modes

The framework detects the current model's context window and adapts automatically:

- **Minimal** (< 128k ctx) → ≤200 tokens injected — Off-context everything. On-demand retrieval.
- **Compact** (128k–256k ctx) → ≤500 tokens — Summary + last reflection + top goals.
- **Standard** (256k+ ctx) → ≤1000 tokens — Full architecture. Monologue stream visible.

## Architecture v0.2.3

```
┌─────────────────────────────────────────────────────────────────────┐
│                       ConsciousnessEngine                           │
│                  (Orchestrator + Lifecycle)                         │
├──────────┬──────────┬──────────┬──────────┬──────────┬────────────┤
│  Inner   │  World   │   Meta   │   Goal   │   Auto   │  Context   │
│ Monologue│  Model   │ Cognition│ Generator│ Evolution│  Manager   │
│          │          │          │          │          │            │
│ Reflect  │ Entities │ Confid.  │ Curiosity│ Propose  │ Mode Det.  │
│ Observe  │ Relations│ BlindSpots│Maintain.│ Approve  │  Budget    │
│ Summarize│ Predicts │  Errors  │ Evolve   │  Apply   │ Injection  │
│          │  Decay   │ Calibrate│ MetaScore│ Observe  │            │
├──────────┴──────────┴──────────┴──────────┴──────────┴────────────┤
│                        v0.2 Modules                                 │
├─────────────┬──────────────┬───────────────┬──────────────────────┤
│ContentStore │  EventBus    │ OutputFilter  │   TokenTracker       │
│             │              │               │                      │
│ FTS5 BM25   │ SHA-256 Dedup│ 8-Stage Pipe  │  chars/4 estimation │
│ Dual Index  │ Priorities   │ StripAnsi     │ Per-source tracking │
│ RRF Merge   │ Expiration   │ CollapseBlank │ Savings % reporting │
│ 8 Categories│ 6 Types      │ MaxLines      │   8 Sources         │
│ SQLite WAL  │ SQLite WAL   │ TruncateLines │   SQLite WAL        │
├─────────────┴──────────────┴───────────────┴──────────────────────┤
│                    v0.2.3 Modules                                   │
├──────────────────────────┬────────────────────────────────────────┤
│  SessionLifecycle        │       SessionRAG (WIP v0.3)            │
│                          │                                        │
│ 6-step pipeline:         │ Semantic search over session DB        │
│  1. Extract from state.db│ Ollama nomic-embed-text (768d)         │
│  2. Enrich w/ Conscio    │ SQLite vector store (numpy cosine)     │
│  3. Emit EventBus event  │ No FAISS — pure numpy                  │
│  4. Index in ContentStore│ 572 lines, compiles clean              │
│  5. Reflect on engine    │ Not yet integrated                     │
│  6. Write heartbeat+ho   │                                        │
│ SQLite WAL + FTS5        │                                        │
├──────────────────────────┴────────────────────────────────────────┤
│                     ModelRegistry                                  │
│              (Model → Context → Mode mapping)                      │
└────────────────────────────────────────────────────────────────────┘
```

## Quick Start

```python
from conscio import ConsciousnessEngine

# Initialize — auto-detects model and mode
engine = ConsciousnessEngine(model_name="glm-5.1")

# Run a reflection cycle
result = engine.reflect(
    world_state="All systems operational",
    confidence=0.8,
    anomalies=["Unusual latency spike detected"],
)

# Get compact state for context injection
injection = engine.get_state_for_injection()

# Query the world model
engine.world.add_entity("server", "system", state="healthy")
engine.world.query("server health")

# Record session lifecycle (on session end/reset)
from conscio import record_session_lifecycle
summary = record_session_lifecycle(
    event_type="session:reset",
    context={"platform": "telegram", "user_id": "123"},
    engine=engine,  # or None to auto-create
)

# Check evolution proposals
proposals = engine.evolution.pending_proposals()

# Properly close resources (SQLite WAL checkpoint)
engine.close()

# Or use as context manager
with ConsciousnessEngine(model_name="glm-5.1") as engine:
    engine.reflect(world_state="Running", confidence=0.7)
    # Resources auto-closed on exit
```

## Session Lifecycle Integration (v0.2.3)

When a Hermes session ends or resets, the `conscio-handoff` hook runs a 6-step pipeline:

1. **Extract** — Read session summary from Hermes `state.db` (intents, actions, reasoning, topics)
2. **Enrich** — Merge Conscio state (world model entities, active goals, meta-confidence, stale entities)
3. **Emit** — Record session event in Conscio EventBus (type=`session`, category=`session`)
4. **Index** — Store heartbeat + handoff in ContentStore (FTS5 searchable by future sessions)
5. **Reflect** — Run post-session reflection on Conscio engine (feeds stale entities + session topics)
6. **Write** — Persist heartbeat (`_latest_heartbeat.md`, <1.5KB) + handoff (`_session_handoff.md`) to disk

**Key properties**:
- On-demand injection: heartbeat read when new session starts, not at fixed time
- Noise filtering: strips compaction artifacts, cron sessions, previous heartbeat injections
- Best-effort enrichment: graceful fallback if Conscio engine methods fail
- Daily compact: single heartbeat file, <1.5KB, overwrites daily
- Zero external deps: stdlib + sqlite3 only

## Active Perception Script

```bash
# Run a single reflection cycle (for cron jobs)
python3 scripts/reflect.py

# With custom world state
python3 scripts/reflect.py --world "Market volatile" --confidence 0.6
```

The `reflect.py` script:
1. Initializes ConsciousnessEngine (with all v0.2 modules)
2. Collects world state from collectors (system, memory, processes)
3. Runs reflection cycle via engine
4. Emits events to EventBus
5. Indexes reflections in ContentStore (FTS5 BM25)
6. Records token usage in TokenTracker
7. Outputs summary + injection for context

## Inner Monologue Loop

```
Every N minutes (configurable):
  1. PERCEIVE  — read world state (logs, APIs, memory, events)
  2. REFLECT   — compare predictions vs reality, assess confidence
  3. GENERATE  — update goals, detect anomalies, identify improvements
  4. PREDICT   — simulate outcomes of potential actions
  5. EVOLVE    — propose modifications (requires human approval)
  6. SUMMARIZE — compress reflection into state (enters context)
  7. EMIT      — broadcast events, index knowledge, track tokens
```

## Module Reference

### Core Modules (v0.1)

- **ConsciousnessEngine** — Central orchestrator. `reflect()`, `perceive()`, `get_state_for_injection()`, `close()`, `record_session_lifecycle()`
- **ContextManager** — Mode detection + token budget allocation
- **ModelRegistry** — Model → context → mode mapping with auto-detection
- **WorldModel** — Entity/relation store with predictions, temporal decay, relevance scoring, pruning
- **MetaCognition** — Confidence tracking, blind spot detection, error pattern frequency, calibration
- **GoalGenerator** — Drive-based goal generation (curiosity, maintenance, evolution) with meta-score
- **AutoEvolution** — Safe self-modification: `propose_skill_patch()`, `observe_errors()`, approval gates
- **InnerMonologue** — Reflection/observe/summarize loop

### v0.2 Modules

- **ContentStore** — FTS5 BM25 dual-index (porter + trigram). RRF merging. 8 categories. SQLite WAL.
- **EventBus** — SHA-256 deduplication. 6 types. 4 priority levels. Event expiration. SQLite WAL.
- **OutputFilter** — 8-stage pipeline: StripAnsi → CollapseBlank → MaxLines → TruncateLines.
- **TokenTracker** — chars/4 estimation. Per-source tracking. Savings percentage. SQLite WAL.
- **Migrator** — JSON → SQLite one-time migration. Validates categories. Rollback on error.

### v0.2.3 Modules

- **SessionLifecycle** — 6-step pipeline for session continuity: extract → enrich → emit → index → reflect → write. Produces heartbeat (<1.5KB) + handoff. Best-effort enrichment with graceful fallback.
- **SessionRAG** (WIP v0.3) — Semantic search over session DB using Ollama nomic-embed-text (768d). SQLite vector store (numpy cosine, no FAISS). 572 lines, compiles clean. Not yet integrated.

### Category/Source/Type Reference

**ContentStore categories:** reflection, perception, trading, system, error, consciousness, external, **session**

**EventBus types:** system, trading, consciousness, external, **session**, error

**TokenTracker sources:** reflection, perception, injection, trading, system, consciousness, tool_output, external

## Safety Rules (Non-Negotiable)

1. **No autonomous self-modification** — all evolution proposals require human approval
2. **Context injection has hard limits** — never exceeds mode budget
3. **Goals are advisory** — internal goals suggest, never execute
4. **Reflections are append-only** — never edited once written
5. **Cannot modify its own safety rules** — no self-referential gate bypass

## Model Registry

| Model | Context | Mode |
|---|---|---|
| GLM 5.1 | 131k | Compact |
| Kimi K2.6 | 256k | Standard |
| MiniMax M2.7 | 260k | Standard |
| Step Flash 3.7 | 260k | Standard |
| Nemotron 3 Super 120B | 1M | Standard |
| Claude Sonnet 4 | 200k | Standard |
| GPT-4o | 128k | Compact |

## Installation

```bash
pip install -e .
```

## Testing

```bash
# Full suite (347 tests)
pytest tests/ -v

# Quick run
pytest tests/ -q

# Specific module
pytest tests/test_consciousness.py -v
pytest tests/test_content_store.py -v
pytest tests/test_event_bus.py -v
pytest tests/test_session_lifecycle.py -v
```

## Database

All SQLite databases use WAL mode for concurrent read/write. Location:

```
~/.hermes/consciousness/
├── conscio.db          # ContentStore + EventBus
├── conscio.db-wal      # Write-ahead log
├── conscio.db-shm      # Shared memory
├── token_tracker.db    # TokenTracker
└── meta_cognition.db   # MetaCognition
```

**Important:** Always call `engine.close()` or use `with` statement to ensure WAL checkpoints.

## Session Continuity System

7 layers of persistence (MEMORY.md → AGENTS.md → Skill → Handoff → MemPalace → Session DB/RAG → Git).

**Hook**: `~/.hermes/hooks/conscio-handoff/handler.py` — fires on `session:end`/`session:reset`
**Cron backup**: `hermet-session-handoff` at 01:30 BRT (before 04:00 BRT daily reset)

**Files produced**:
- `~/mempalace/diary/_latest_heartbeat.md` — compact (<1.5KB), auto-injected on next session
- `~/mempalace/diary/_session_handoff.md` — richer version for manual reference
- `~/mempalace/diary/heartbeat_YYYYMMDD_HHMM.md` — dated archive

## Audit History

- **v0.2.3 (2026-06-05)** — Session lifecycle integration. Added `session` type/category to EventBus/ContentStore. New `session_lifecycle.py` module with 6-step pipeline (extract → enrich → emit → index → reflect → write). Rewritten hook handler + heartbeat generator. 31 new tests. **347 total tests.**
- **v0.2.2 (2026-06-05)** — Session handoff system + on-demand heartbeat injection. AGENTS.md boot instructions. SessionRAG stub (572 lines).
- **v0.2.1 (2026-06-05)** — Full audit of 14 modules + 6 test files (~8400 lines). Found and fixed 3 bugs: OutputFilter config keys, missing lifecycle cleanup, dead import. Added 3 regression tests. 316 tests.
- **v0.2.0 (2026-06-04)** — Integration audit. Fixed EventBus/ContentStore/TokenTracker API call signatures in engine.py and reflect.py. 313 tests.
- **v0.1.0 (2026-06-03)** — Initial release. 313 tests.

## License

MIT — Neguiolidas / Neguitech
