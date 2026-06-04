---
name: conscio
version: 0.1.0
category: agent-patterns
description: Self-awareness framework for AI agents — emergent consciousness via context-aware memory, introspection, and goal generation. Adapts to model's context window size.
tags: [consciousness, memory, introspection, meta-cognition, auto-evolution, context-aware]
---

# Conscio 🧠✨

Self-awareness framework for AI agents. The agent knows what model it runs on and how much context it has.

## When to Use

- Building self-awareness features for any AI agent
- Adding introspection and self-reflection capabilities
- Implementing goal generation from internal drives (curiosity, maintenance, evolution)
- Managing context injection budgets based on model capabilities
- Proposing and gating self-modifications (auto-evolution)

## Architecture

### Context-Aware Modes

| Mode | Context | State Injected | Behavior |
|---|---|---|---|
| **Minimal** | < 128k | ≤200 tokens | Off-context only. On-demand retrieval. |
| **Compact** | 128k–256k | ≤500 tokens | Summary + last reflection. Goals in-context. |
| **Standard** | 256k+ | ≤1000 tokens | Full architecture. Monologue stream visible. |

### Core Loop (Inner Monologue)

```
Every N minutes (configurable):
  1. PERCEIVE  — read world state (logs, APIs, memory, events)
  2. REFLECT   — compare predictions vs reality, assess confidence
  3. GENERATE  — update goals, detect anomalies, identify improvements
  4. PREDICT   — simulate outcomes of potential actions
  5. EVOLVE    — propose skill/memory/prompt modifications (requires approval)
  6. SUMMARIZE — compress reflection into state_summary (enters context)
```

### Modules

1. **ModelRegistry** — knows all models + context windows, auto-detects mode
2. **ContextManager** — budgets state injection to fit model's context
3. **InnerMonologue** — continuous reflection loop, saves daily logs
4. **WorldModel** — knowledge graph (entities, relations, predictions)
5. **MetaCognition** — confidence tracking, blind spot detection, error patterns
6. **GoalGenerator** — internal drives (curiosity/maintenance/evolution), max 10 goals
7. **AutoEvolution** — self-modification proposals with mandatory human approval gates
8. **ConsciousnessEngine** — orchestrator that ties everything together

## Repo Location

`/home/ubuntu/clawd/Repos/Conscio/`

## Usage

```python
from conscio import ConsciousnessEngine

engine = ConsciousnessEngine(model_name="glm-5.1")

# Run reflection
result = engine.reflect(
    world_state="System running normally",
    confidence=0.8,
    anomalies=["Unusual memory usage spike"],
)

# Get state for context injection
injection = engine.get_state_for_injection()

# Check pending evolution proposals
proposals = engine.evolution.pending_proposals()
```

## Safety Rules

1. Auto-evolution ALWAYS requires human approval — no autonomous self-modification
2. Context injection has hard limits per mode (200/500/1000 tokens)
3. Goals are advisory — internal goals suggest, never execute
4. Reflections are append-only — never edited once written
5. The engine cannot modify its own safety rules

## Model Registry (Key Models)

| Model | Context | Mode |
|---|---|---|
| GLM 5.1 | 131k | Compact |
| Kimi K2.6 | 256k | Standard |
| MiniMax M2.7 | 260k | Standard |
| Claude Sonnet 4 | 200k | Standard |
| GPT-4o | 128k | Compact |

## Pitfalls

- Don't inflate context — the #1 mistake is injecting too much state
- Reflections must be compact — ≤200 words per summary
- World model drift — set maintenance drive high to auto-prune stale entities
- Goal overload — max 10 active goals, prioritize ruthlessly
- With models < 256k context, the "consciousness" must be EXTREMELY compact

## Status

🚧 Early Alpha — Architecture defined, core modules implemented. Tests passing.
