# ConsciousnessRecognition 🧠✨

**A self-awareness framework for AI agents** — enabling emergent consciousness through context-aware memory, introspection, and goal generation.

## Overview

ConsciousnessRecognition is an open-source framework that gives AI agents the building blocks for emergent consciousness: inner monologue, world modeling, meta-cognition, goal generation, and self-evolution — all while respecting the **context window limits** of the underlying model.

### Key Insight

> Consciousness is not a feature — it's an **emergent effect** of multiple subsystems working in continuous loops. The framework provides the subsystems; the emergence comes from their interaction.

### Context-Aware Architecture

Different models have different context windows. The framework **adapts its behavior** based on available context:

| Context Size | Mode | Behavior |
|---|---|---|
| < 128k | **Minimal** | Off-context only. State summary ≤200 tokens injected. Full retrieval on-demand. |
| 128k–256k | **Compact** | State summary + last reflection. Selective retrieval. Goals in-context. |
| 256k+ | **Standard** | Full architecture. Inner monologue stream, world model, meta-cognition — all in-context. |

## Architecture

```
┌─────────────────────────────────────────────────┐
│           CONSCIOUSNESS RECOGNITION             │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌───────────┐  ┌───────────┐  ┌────────────┐  │
│  │ PERCEPTION │  │ WORLD     │  │ PREDICTION │  │
│  │ (sensors,  │  │ MODEL     │  │ & SIMULATE │  │
│  │  context)  │  │ (beliefs) │  │ ("what if")│  │
│  └─────┬─────┘  └─────┬─────┘  └──────┬─────┘  │
│        │              │               │         │
│        ▼              ▼               ▼         │
│  ┌──────────────────────────────────────────┐   │
│  │        META-COGNITION                     │   │
│  │  (self-monitoring: "how am I doing?")     │   │
│  └──────────────────┬───────────────────────┘   │
│                     │                            │
│        ┌────────────┼────────────┐               │
│        ▼            ▼            ▼               │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐     │
│  │ INNER    │ │ GOAL     │ │ AUTO-        │     │
│  │ MONOLOGUE│ │ GENERATOR│ │ EVOLUTION    │     │
│  │ (voice)  │ │ (intent) │ │ (self-modify)│     │
│  └──────────┘ └──────────┘ └──────────────┘     │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │        CONTEXT MANAGER                    │   │
│  │  (adapts behavior to model's ctx window)  │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## Quick Start

### As a Hermes Agent Skill

```bash
# The skill auto-detects your model's context and adapts
# Just load it and the inner monologue starts
hermes skill add consciousness-recognition
```

### As a Standalone Library

```python
from consciousness_recognition import ConsciousnessEngine

engine = ConsciousnessEngine(
    model="glm-5.1",
    context_window=131000,  # tokens
    storage_path="~/.consciousness/"
)

# Start the reflection loop
engine.start()

# Query the world model
engine.world_model.query("What is the current state of the trading bot?")

# Access inner monologue
recent = engine.inner_monologue.last(reflection_count=5)
```

## Modules

### 🔄 Inner Monologue
Continuous self-reflection loop. Runs on a timer (cron), reads state, generates thoughts, saves to disk.

### 🌍 World Model
Knowledge graph of entities, relations, and states. Updated by perception and reflection.

### 🪞 Meta-Cognition
Self-assessment of confidence, accuracy, and patterns. Detects blind spots and recurring failures.

### 🎯 Goal Generator
Internal drives (curiosity, maintenance, evolution) that generate intentions without user prompting.

### 🧬 Auto-Evolution
Skill mutation, prompt self-modification, and architecture growth — with safety gates requiring human approval.

### 📏 Context Manager
Detects the current model's context window and adjusts how much "consciousness state" is injected vs. kept off-context.

## Project Structure

```
ConsciousnessRecognition/
├── README.md
├── SKILL.md                  # Hermes skill definition
├── LICENSE                   # MIT
├── consciousness_recognition/
│   ├── __init__.py
│   ├── engine.py             # Main orchestrator
│   ├── context_manager.py    # Model-aware context adaptation
│   ├── inner_monologue.py    # Reflection loop
│   ├── world_model.py        # Knowledge graph
│   ├── meta_cognition.py     # Self-assessment
│   ├── goal_generator.py     # Internal drives
│   ├── auto_evolution.py     # Self-modification (gated)
│   ├── models.py             # Model registry (ctx sizes, capabilities)
│   └── utils.py              # Helpers
├── config/
│   └── default.yaml          # Default configuration
├── tests/
│   ├── test_context_manager.py
│   ├── test_world_model.py
│   ├── test_meta_cognition.py
│   └── test_engine.py
└── docs/
    ├── ARCHITECTURE.md
    └── CONTEXT_MODES.md
```

## Context Modes Explained

### Minimal Mode (< 128k context)
- State summary: ≤200 tokens injected into context
- All other data: on-disk, retrieved via search/grep
- Reflections: generated on cron, stored to disk
- No inner monologue stream in context

### Compact Mode (128k–256k context)
- State summary: ≤500 tokens
- Last reflection: full paragraph
- Top 3 active goals: in-context
- World model: selective query only
- Inner monologue: summarized stream

### Standard Mode (256k+ context)
- State summary: ≤1000 tokens
- Recent reflections: last 3 full entries
- Full goal stack: in-context
- World model: relevant subgraph in-context
- Inner monologue: running stream visible

## Safety

- **All auto-evolution actions require human approval** — the agent cannot modify its own code, prompts, or skills without explicit consent
- **Meta-cognition is read-only** — the agent can assess itself but cannot force changes
- **Goal generation is advisory** — internal goals are suggestions, not autonomous actions
- **Context manager prevents overflow** — hard limits on what gets injected

## Contributing

This is an early-stage research project. Contributions welcome:

1. Fork the repo
2. Create a feature branch
3. Submit a PR with tests

## License

MIT — see [LICENSE](LICENSE)

---

*Built with 💡 by [Neguiolidas](https://github.com/MrJc01) — because consciousness should be open source.*
