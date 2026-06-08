# Conscio 🧠✨

Self-awareness framework for AI agents — emergent consciousness via FTS5 knowledge base, event tracking, output filtering, token tracking, and goal-driven auto-evolution. 100% local Python/SQLite.

## Features

- **Persistent Memory** — FTS5 BM25 + trigram search over reflections, events, and knowledge
- **Self-Awareness** — model detection, context window mapping, metabolic tier assessment
- **Goal System** — drives (curiosity, maintenance, evolution) generate goals from internal state
- **Session Continuity** — heartbeat + handoff files survive across session resets (optional)
- **Output Compression** — 8-stage YAML-driven filter pipeline
- **World Model** — knowledge graph with temporal decay, entropy scoring, semantic contradiction detection
- **Dream Cycle** — maintenance: release duplicates → prune stale → friction → crystallize
- **Shard Engine** — 7 cognitive modes (ARCHITECT → DREAMER), keyword-based inference
- **Coherence Engine** — recursive-coherence state metric with voice presets
- **Semantic Reconciliation** — antonym-axis contradiction detection + semantic dedup

## Install

```bash
pip install conscio
```

Or from source:

```bash
git clone https://github.com/Neguiolidas/Conscio.git
cd Conscio && pip install -e .
```

**Requirements**: Python 3.10+, numpy>=1.24. Optional: [Ollama](https://ollama.ai) (for embeddings — degrades gracefully without it).

## Quick Start

```python
from conscio import ConsciousnessEngine

# Engine orchestrates all modules — ALWAYS close it
with ConsciousnessEngine(model_name="my-agent-v1") as engine:
    result = engine.reflect(world_state="...", confidence=0.8, anomalies=[])
    injection = engine.get_state_for_injection()
    results = engine.recall("API timeout patterns", k=5)
    engine.perceive(session_id="sess-123")
```

### ContentStore — FTS5 knowledge base

```python
from conscio.content_store import ContentStore

with ContentStore() as store:
    store.index(label="incident-log", content="API timeout on /v2/trades", category="trading")
    results = store.search("API timeout", limit=5)
```

### EventBus — session event tracking

```python
from conscio.event_bus import EventBus

with EventBus() as bus:
    eid = bus.emit("error", "trading", {"pattern": "API timeout"})  # returns int
    events = bus.query(category="trading", limit=10)
```

### OutputFilter — compression pipeline

```python
from conscio.output_filter import build_pipeline_from_dict

pipeline = build_pipeline_from_dict({
    "strip_ansi": {},
    "truncate": {"max_chars": 4000},
})
filtered = pipeline.apply(raw_text)
```

## Session Handoff (Optional)

Conscio can write heartbeat + handoff files so your agent survives session resets.

```python
from conscio import record_session_lifecycle

# Default: uses HERMES_HOME/state.db + ~/mempalace/diary/
summary = record_session_lifecycle("session:end", context={"session_id": "sess-123"})

# Custom paths (any agent)
from pathlib import Path
summary = record_session_lifecycle(
    "session:end", context={"session_id": "sess-123"},
    session_db=Path("/my/agent/state.db"),
    handoff_dir=Path("/my/agent/handoffs"),
)

# Disable file writes entirely
summary = record_session_lifecycle(
    "session:end", context={"session_id": "sess-123"},
    handoff_dir=None,  # pipeline still runs (EventBus, ContentStore, reflect)
)
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `CONSCIO_SESSION_DB` | Override session database path |
| `CONSCIO_HANDOFF_DIR` | Override handoff directory (`none`/`off`/`false`/`0` to disable) |
| `HERMES_HOME` | Base directory for Hermes Agent (default `~/.hermes`) |

## Architecture

```
ConsciousnessEngine (orchestrator)
├── ContentStore      FTS5 BM25 + trigram knowledge base
├── EventBus          Session event tracking with dedup
├── OutputFilter      8-stage YAML compression pipeline
├── TokenTracker      Char/4 token estimation + budget
├── WorldModel        Knowledge graph (temporal decay + entropy)
├── MetaCognition     Confidence tracking, blind spots, error patterns
├── GoalGenerator     Drives: curiosity, maintenance, evolution
├── AutoEvolution     Self-modification proposals (human approval gates)
├── InnerMonologue    Continuous reflection loop (FTS5-backed)
├── DreamCycle        Maintenance: release → prune → friction → crystallize
├── CoherenceEngine   Recursive-coherence state metric
├── ShardEngine       7 cognitive modes, keyword inference
├── SemanticEngine    Antonym-axis contradiction detection
├── SessionRAG        Semantic search over sessions (Ollama optional)
├── ContentLayerManager  Unified recall: FTS5 + SessionRAG + WorldModel
└── SessionLifecycle  Session continuity: heartbeat + handoff + enrichment
```

## Context Modes

| Mode | Context Window | Budget | What's Injected |
|------|---------------|--------|-----------------|
| Minimal | < 128k | 200 tok | Summary only |
| Compact | 128k–256k | 500 tok | Summary + reflection + top goals |
| Standard | 256k+ | 1000 tok | Full state + world subgraph |

## Safety

- **No autonomous self-modification** — all proposals need human approval
- Context injection has hard limits per mode
- Goals are advisory, never execute
- Reflections are append-only and searchable
- OutputFilter never breaks workflow — fallback to raw on any stage failure

## Development

```bash
pip install -e ".[dev]"
pytest                                        # run all tests
pytest tests/test_content_store.py -v         # run specific module
pytest -k "not test_recall_graceful" -q       # skip known issue
```

## License

MIT License — Copyright (c) 2026 Neguiolidas / Neguitech
