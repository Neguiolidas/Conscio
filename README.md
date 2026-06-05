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

## Context-Aware Modes

The framework detects the current model's context window and adapts automatically:

- **Minimal** (< 128k ctx) → ≤200 tokens injected — Off-context everything. On-demand retrieval.
- **Compact** (128k–256k ctx) → ≤500 tokens — Summary + last reflection + top goals.
- **Standard** (256k+ ctx) → ≤1000 tokens — Full architecture. Monologue stream visible.

## Architecture v0.2

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ConsciousnessEngine                            │
│                     (Orchestrator + Lifecycle)                     │
├──────────┬──────────┬──────────┬──────────┬──────────┬────────────┤
│  Inner   │  World   │   Meta   │   Goal   │   Auto   │  Context   │
│ Monologue│  Model   │ Cognition│ Generator│ Evolution│  Manager   │
│          │          │          │          │          │            │
│ Reflect  │ Entities │ Confid.  │ Curiosity│ Propose  │ Mode Det.  │
│ Observe  │ Relations│ BlindSpots│Maintain.│ Approve  │ Budget     │
│ Summarize│ Predicts │ Errors   │  Evolve  │  Apply   │ Injection  │
│          │ Decay    │Calibrate │MetaScore │ Observe  │            │
├──────────┴──────────┴──────────┴──────────┴──────────┴────────────┤
│                        v0.2 Modules                                │
├─────────────┬──────────────┬───────────────┬──────────────────────┤
│ContentStore │   EventBus   │ OutputFilter  │    TokenTracker      │
│             │              │               │                      │
│ FTS5 BM25   │ SHA-256 Dedup│ 8-Stage Pipe  │ chars/4 estimation   │
│ Dual Index  │ Priorities   │ StripAnsi     │ Per-source tracking  │
│ RRF Merge   │ Expiration   │ CollapseBlank │ Savings % reporting  │
│ 7 Categories│ 4 Categories │ MaxLines      │ 8 Sources            │
│ SQLite WAL  │ SQLite WAL   │ TruncateLines │ SQLite WAL           │
├─────────────┴──────────────┴───────────────┴──────────────────────┤
│                        ModelRegistry                               │
│                  (Model → Context → Mode mapping)                  │
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

# Check evolution proposals
proposals = engine.evolution.pending_proposals()

# Properly close resources (SQLite WAL checkpoint)
engine.close()

# Or use as context manager
with ConsciousnessEngine(model_name="glm-5.1") as engine:
    engine.reflect(world_state="Running", confidence=0.7)
    # Resources auto-closed on exit
```

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
  1. PERCEIVE — read world state (logs, APIs, memory, events)
  2. REFLECT — compare predictions vs reality, assess confidence
  3. GENERATE — update goals, detect anomalies, identify improvements
  4. PREDICT — simulate outcomes of potential actions
  5. EVOLVE — propose modifications (requires human approval)
  6. SUMMARIZE — compress reflection into state (enters context)
  7. EMIT — broadcast events, index knowledge, track tokens
```

## Module Reference

### Core Modules (v0.1)

- **ConsciousnessEngine** — Central orchestrator. `reflect()`, `perceive()`, `get_state_for_injection()`, `close()`
- **ContextManager** — Mode detection + token budget allocation
- **ModelRegistry** — Model → context → mode mapping with auto-detection
- **WorldModel** — Entity/relation store with predictions, temporal decay, relevance scoring, pruning
- **MetaCognition** — Confidence tracking, blind spot detection, error pattern frequency, calibration
- **GoalGenerator** — Drive-based goal generation (curiosity, maintenance, evolution) with meta-score
- **AutoEvolution** — Safe self-modification: `propose_skill_patch()`, `observe_errors()`, approval gates
- **InnerMonologue** — Reflection/observe/summarize loop

### v0.2 Modules

- **ContentStore** — FTS5 BM25 dual-index (porter + trigram). RRF merging. 7 categories. SQLite WAL.
- **EventBus** — SHA-256 deduplication. 4 priority levels. Event expiration. SQLite WAL.
- **OutputFilter** — 8-stage pipeline: StripAnsi → CollapseBlank → MaxLines → TruncateLines.
- **TokenTracker** — chars/4 estimation. Per-source tracking. Savings percentage. SQLite WAL.
- **Migrator** — JSON → SQLite one-time migration. Validates categories. Rollback on error.

### Category/Source Reference

**ContentStore categories:** reflection, perception, trading, system, error, consciousness, external

**EventBus categories:** system, trading, consciousness, external

**TokenTracker sources:** reflection, perception, injection, trading, system, consciousness, tool_output, external

## Safety Rules (Non-Negotiable)

1. **No autonomous self-modification** — all evolution proposals require human approval
2. **Context injection has hard limits** — never exceeds mode budget
3. **Goals are advisory** — internal goals suggest, never execute
4. **Reflections are append-only** — never edited once written
5. **Cannot modify its own safety rules** — no self-referential gate bypass

## Model Registry

- GLM 5.1 — 131k ctx — Compact mode
- Kimi K2.6 — 256k ctx — Standard mode
- MiniMax M2.7 — 260k ctx — Standard mode
- Step Flash 3.7 — 260k ctx — Standard mode
- Nemotron 3 Super 120B — 1M ctx — Standard mode
- Claude Sonnet 4 — 200k ctx — Standard mode
- GPT-4o — 128k ctx — Compact mode

## Installation

```bash
pip install -e .
```

## Testing

```bash
# Full suite (316 tests)
pytest tests/ -v

# Quick run
pytest tests/ -q

# Specific module
pytest tests/test_consciousness.py -v
pytest tests/test_content_store.py -v
pytest tests/test_event_bus.py -v
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

## Audit History

- **v0.2.1 (2025-06-05)** — Full audit of 14 modules + 6 test files (~8400 lines). Found and fixed 3 bugs:
  OutputFilter config keys, missing lifecycle cleanup, dead import. Added 3 regression tests.
- **v0.2.0 (2025-06-04)** — Integration audit. Fixed EventBus/ContentStore/TokenTracker API call
  signatures in engine.py and reflect.py. 313 tests passing.
- **v0.1.0 (2025-06-03)** — Initial release. 313 tests.

## License

MIT — Neguiolidas / Neguitech
