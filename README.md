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

## Context-Aware Modes

The framework detects the current model's context window and adapts automatically:

| Mode | Context Window | State Injected | Behavior |
|---|---|---|---|
| **Minimal** | < 128k | ≤200 tokens | Off-context everything. On-demand retrieval. |
| **Compact** | 128k–256k | ≤500 tokens | Summary + last reflection + top goals. |
| **Standard** | 256k+ | ≤1000 tokens | Full architecture. Monologue stream visible. |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  ConsciousnessEngine                  │
│                   (Orchestrator)                      │
├──────────┬──────────┬──────────┬──────────┬──────────┤
│  Inner   │  World   │   Meta   │   Goal   │   Auto   │
│ Monologue│  Model   │ Cognition│ Generator│ Evolution│
│          │          │          │          │          │
│ Reflect  │ Entities │ Confid.  │ Curiosity│ Propose  │
│ Observe  │ Relations│ BlindSpots│Maintain.│ Approve  │
│ Summarize│ Predicts │ Errors   │ Evolve   │ Apply    │
├──────────┴──────────┴──────────┴──────────┴──────────┤
│                ContextManager                         │
│          (Mode Detection + Budget)                    │
├──────────────────────────────────────────────────────┤
│                ModelRegistry                          │
│         (Model → Context → Mode mapping)              │
└──────────────────────────────────────────────────────┘
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
```

## Inner Monologue Loop

```
Every N minutes (configurable):
  1. PERCEIVE  — read world state (logs, APIs, memory, events)
  2. REFLECT   — compare predictions vs reality, assess confidence
  3. GENERATE  — update goals, detect anomalies, identify improvements
  4. PREDICT   — simulate outcomes of potential actions
  5. EVOLVE    — propose modifications (requires human approval)
  6. SUMMARIZE — compress reflection into state (enters context)
```

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
pytest tests/ -v
```

## License

MIT — Neguiolidas / Neguitech
