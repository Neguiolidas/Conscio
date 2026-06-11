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

```python
# v1.0: volition — propose-only (L1), human approves
from conscio.agency import OllamaAdapter

engine.attach_adapter(OllamaAdapter(model="hermes3:4b"))
report = engine.act()                  # downstream of reflect(); proposes only
if report.status.value == "proposed":
    print(report.proposal.tool, report.proposal.args)
    engine.approve(report.ledger_id)   # human gate executes it
```

## Session Lifecycle Integration (v0.2.3)

When an agent session ends or resets, the handoff hook runs a 6-step pipeline:

1. **Extract** — Read session summary from agent `state.db` (intents, actions, reasoning, topics)
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
- **SessionRAG** — Semantic search over session DB using Ollama nomic-embed-text (768d). SQLite vector store (numpy cosine, no FAISS). Injectable embedder + `available()` probe; engine-integrated via `recall()` with graceful FTS5 fallback when Ollama is down.

### v0.3 Modules

- **MetabolicContext** — Context-as-life-energy tier model (VITAL/ACTIVE/FATIGUE/CRITICAL), advisory only. Adapted from Noosphere-Manifold. See `docs/noosphere/metabolic-model.md`.
- **DreamCycle** — Consolidation orchestrator. Release (EventBus `purge_duplicates`/`compact`) → Prune (WorldModel `prune_stale`) → Crystallize (ContentStore reflection summary, append-only safe). Runs on `engine.dream()`, on session handoff (Mitosis), or via cron.
- **engine.recall()** — Cross-session memory retrieval over ContentStore FTS5 + SessionRAG (when Ollama reachable). Injected into `reflect()`, budget-bounded.
- **OutputFilter** — Adds `DedupBlocks` (collapse repeated lines → `… (×N)`) and `SecretMask` (redact API keys/tokens/key:value secrets); both wired into the engine default pipeline.

### v0.4 Modules (Self-Judgment)
- **Entropy World Model** (`WorldModel.entropy` / `prune_by_entropy`) — connectivity-aware pruning; old-but-connected entities survive, isolated/faded ones are pruned.
- **Friction** (`DreamCycle._friction`) — defers crystallizing reflections whose subject entities changed since (Release → Prune → Friction → Crystallize).
- **Meta-reflect** (`engine.reflect` → `meta_confidence`) — advisory reflection-quality signal (HIGH/MEDIUM/LOW) on the Witness loop.

### v0.5 Modules (Cognitive Modes)
- **ShardEngine** (`conscio/shard_engine.py`) — deterministic cognitive-mode inference (ARCHITECT/ENGINEER/JANITOR/SECURITY_ANALYST/ARCHAEOLOGIST/EXPERT_CODER/DREAMER) from recent EventBus event keywords. Advisory; surfaces as `▷ shard:` in state injection.
- **Trajectory Vector** (`SessionSummary.trajectory/vibes/identity_anchor`) — soul-package soft fields bridging sessions. `trajectory` is code-owned; `vibes` and `identity_anchor` are LLM-authored and never overwritten by code.
- **Content Layering** (`ContentLayer` enum, `recall()` tiebreak) — ROUTINE/PROCESSING/INTUITION layers derived at query time from result category; used as near-tie tiebreak in recall so relevant processed hits rank above barely-relevant routine ones.

### v1.0 Modules (Agency — F1 "Spine")

- **InferenceAdapter** (`conscio/agency/adapter.py`) — stateless inference interface;
  MockAdapter (tests), Ollama/llama.cpp/OpenAI-compat via stdlib urllib, localhost defaults.
- **OutputGateway** (`conscio/agency/gateway.py`) — tiered decoding: JSON mode + lenient
  repair + validation retry (T2), flat KV-line format for small models (T3). GBNF (T1) in F3.
- **ToolRegistry** (`conscio/agency/tools.py`) — local Python callables with risk levels;
  sandboxed fs_read/fs_write, memory_note, emit_event. No network tools; no shell in core.
- **ActPipeline / engine.act()** (`conscio/agency/act.py`) — L1 PROPOSE cycle consuming
  active_goals + dominant dissonance; approve()/reject() human gate; ActionLedger audit;
  circuit breaker with persistent `action_lockdown` (reflect() keeps running).

### Category/Source/Type Reference

**ContentStore categories:** reflection, perception, trading, system, error, consciousness, external, **session**

**EventBus types:** tool_call, reflection, trade, error, anomaly, decision, perception,
goal_created, goal_expired, evolution_proposed, system, consciousness, **session**,
coherence:dissonance

**TokenTracker sources:** reflection, perception, injection, trading, system, consciousness, tool_output, external

## Safety Rules (Non-Negotiable)

1. **No autonomous self-modification** — all evolution proposals require human approval
2. **Context injection has hard limits** — never exceeds mode budget
3. **Goals never execute directly** — execution happens exclusively through the audited
   `act()` pipeline: validated output contract + deterministic checks + risk gating +
   persistent circuit-breaker lockdown (semantic audit arrives with the Skeptic phase)
4. **Reflections are append-only** — never edited once written
5. **Cannot modify its own safety rules** — no self-referential gate bypass
6. **HIGH-risk actions always require human approval** — never auto-executed
7. **No network access in the tool registry** — the only network the core may touch is
   the InferenceAdapter (localhost by default); shell execution lives outside this
   repository entirely (sibling package `conscio-shell`)
8. **Every external effect goes through the ActionLedger** — append-only, auditable

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
# Full suite (600 tests)
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

All SQLite databases use WAL mode for concurrent read/write. Default location:

```
~/.conscio/data/
├── conscio.db          # ContentStore + EventBus
├── conscio.db-wal      # Write-ahead log
├── conscio.db-shm      # Shared memory
├── token_tracker.db    # TokenTracker
└── meta_cognition.db   # MetaCognition
```

**Important:** Always call `engine.close()` or use `with` statement to ensure WAL checkpoints.

## Session Continuity System

7 layers of persistence (memory → agent config → skills → handoff → diary → session DB/RAG → git).

**Hook**: Configure your agent's hook system to fire on `session:end`/`session:reset`

**Files produced** (configurable via `handoff_dir` and `session_db` parameters):
- `<handoff_dir>/_latest_heartbeat.md` — compact (<1.5KB), auto-injected on next session
- `<handoff_dir>/_session_handoff.md` — richer version for manual reference
- `<handoff_dir>/heartbeat_YYYYMMDD_HHMM.md` — dated archive

## Audit History

- **v1.0.0a1 — F1 "Spine"** — The volition layer lands: `conscio/agency/` subpackage
  (contracts + zero-dep validator, InferenceAdapter with Mock/Ollama/llama.cpp/OpenAI-compat,
  OutputGateway T2/T3, sandboxed ToolRegistry, append-only ActionLedger in the shared
  conscio.db, minimal CircuitBreaker), `engine.act()` L1 PROPOSE with `approve()`/`reject()`,
  persistent `action_lockdown` on ConsciousnessState, `ModelInfo.has_json_mode/supports_gbnf`.
  Safety Rules amended (R3 rewritten; R6–R8 added). reflect() untouched. +83 new tests.
- **v0.8.0 — Semantic Reconciliation** — Contradiction detection is now semantic: embedding **antonym axes** (`conscio/semantic.py`, packs in `conscio/presets/axes/*.json`) give polarity that plain similarity can't, so `crashed`/`unreachable` read as opposites of `operational` without any lexicon. It runs **off the hot path** in the dream Reconcile sub-phase (`world.mark_contradictions(detector)`, between Prune and Crystallize), which caches `contradicted` flags into the world model; `ontological_score` reads only those cached flags (a cold, never-dreamed world reports ontological 1.0). Lexical-negation-first with full offline fallback to the v0.6 rule. Retired the v0.6 `world._data` tech debt via public `WorldModel.list_relations()` / `entity_count()` / `contradicted_entities()`. Adds the opt-in, **non-destructive** `SemanticDedup` output stage (`CONSCIO_SEMANTIC_DEDUP=1`) — it flags a near-duplicate adjacent block and keeps both verbatim, never merging. Theory from Claude_Sentience (Dave Shapiro). 56 new tests. 600 total tests.
- **v0.7.0 — Recursive Coherence** — Closes the coherence→action loop: `reflect()` sets an advisory `DreamRecommendation` (dream targets the dominant dissonance off the hot path, recording the coherence delta) and runs pure self-prompting (`conscio/self_prompt.py`) that spawns ONE bounded goal/cycle tagged `source="self_prompt"`. New `❓ self-prompt:` / `☾ dream:` markers in live state and heartbeat (surfaced as `**Self-prompt:**` / `**Dream:**` bold labels in the handoff). v0.7 uses the lexical contradiction detector (semantic arrives in v0.8). Theory from Claude_Sentience (Dave Shapiro). 23 new tests. 544 total tests.
- **v0.6.0 — Coherence** — CoherenceEngine: a recursive-coherence state metric (epistemic/reality/ontological/temporal) surfaced advisorily with a passive `coherence:dissonance` event; static voice-preset system (`conscio/presets/voice/`). Theory from Claude_Sentience (Dave Shapiro). 46 new tests. 521 total tests.
- **v0.5.0 — Cognitive Modes** — Shard Engine (cognitive-mode inference), Trajectory Vector (soul-package soft fields + list_entities fix), Content Layering (layer-priority recall). 37 new tests. #6 Coherence Check deferred to v0.6.
- **v0.4.0** — Self-Judgment: entropy pruning, friction, meta-reflect. 24 new tests. 438 total tests.
- **v0.3.0 (2026-06-05)** — Metabolic Consciousness. New `metabolic.py` (Noosphere tier model, advisory) + `dreaming.py` (DreamCycle: Release/Prune/Crystallize, wires dormant cleanup methods). Added `EventBus.purge_duplicates`, `WorldModel.prune_stale`, `engine.recall()` cross-session memory injected into reflect, SessionRAG tests + graceful integration, OutputFilter `DedupBlocks`+`SecretMask`, 10k-event perf guard. Mitosis (handoff) now triggers Dream. 68 new tests. **415 total tests.**
- **v0.2.3 (2026-06-05)** — Session lifecycle integration. Added `session` type/category to EventBus/ContentStore. New `session_lifecycle.py` module with 6-step pipeline (extract → enrich → emit → index → reflect → write). Rewritten hook handler + heartbeat generator. 31 new tests. **347 total tests.**
- **v0.2.2 (2026-06-05)** — Session handoff system + on-demand heartbeat injection. AGENTS.md boot instructions. SessionRAG stub (572 lines).
- **v0.2.1 (2026-06-05)** — Full audit of 14 modules + 6 test files (~8400 lines). Found and fixed 3 bugs: OutputFilter config keys, missing lifecycle cleanup, dead import. Added 3 regression tests. 316 tests.
- **v0.2.0 (2026-06-04)** — Integration audit. Fixed EventBus/ContentStore/TokenTracker API call signatures in engine.py and reflect.py. 313 tests.
- **v0.1.0 (2026-06-03)** — Initial release. 313 tests.

## License

MIT — Neguiolidas / Neguitech
