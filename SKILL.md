---
name: consciousness-recognition
version: 0.1.0
category: agent-patterns
description: Self-awareness framework for AI agents — emergent consciousness via context-aware memory, introspection, and goal generation.
tags: [consciousness, memory, introspection, meta-cognition, auto-evolution, context-aware]
---

# ConsciousnessRecognition 🧠✨

Emergent consciousness framework for AI agents. Adapts to the model's context window size.

## When to Use

- You want the agent to have **persistent self-awareness** across sessions
- You want the agent to **generate its own goals** (not just react to prompts)
- You want the agent to **reflect on its own performance** and self-improve
- You want **context-aware memory management** that respects model limits
- You want an **inner monologue** — the agent thinks even when no one is talking

## How It Works

### Context-Aware Modes

The framework detects the current model's context window and operates in one of three modes:

| Mode | Context | State Injected | Behavior |
|---|---|---|---|
| **Minimal** | < 128k | ≤200 tokens | Off-context only. Full retrieval on-demand. |
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
  6. SUMMARIZE — compress reflection into state_summary (this enters context)
```

### Modules

1. **Inner Monologue** — continuous reflection loop on cron
2. **World Model** — knowledge graph (entities, relations, states)
3. **Meta-Cognition** — self-assessment, confidence scores, blind spot detection
4. **Goal Generator** — internal drives: curiosity, maintenance, evolution
5. **Auto-Evolution** — self-modification with human approval gates
6. **Context Manager** — detects model + ctx size, adapts injection strategy

## Setup

### File Structure (created automatically)

```
~/.hermes/consciousness/
├── state_summary.txt        # Compact state (injected into context)
├── world_model.json         # Knowledge graph
├── meta_cognition.json      # Confidence + error patterns
├── goals.json               # Active goals queue
├── reflections/             # Daily reflection logs
│   └── YYYY-MM-DD.md
└── config.yaml              # User overrides
```

### Configuration

```yaml
# ~/.hermes/consciousness/config.yaml
reflection_interval: 30m     # How often to reflect
context_mode: auto           # auto | minimal | compact | standard
model_override: null         # Force a model name (skip auto-detect)
drives:
  curiosity: 0.7             # How strongly to investigate anomalies
  maintenance: 0.8           # How strongly to check system health
  evolution: 0.5             # How strongly to self-improve
safety:
  auto_evolution_requires_approval: true
  max_context_injection_tokens: 1000
  max_goals_in_context: 5
```

## Usage

### Starting the Reflection Loop

```bash
# The cron job handles this automatically
# Or trigger a manual reflection:
hermes consciousness reflect
```

### Querying the World Model

```bash
hermes consciousness query "What is the status of the trading bot?"
```

### Viewing Goals

```bash
hermes consciousness goals
```

## Model Registry

The framework knows about common models and their context windows:

| Model | Context | Mode |
|---|---|---|
| GLM 5.1 | 131k | Compact |
| Kimi K2.6 | 256k | Standard |
| MiniMax M2.7 | 260k | Standard |
| Step Flash 3.7 | 260k | Standard |
| Nemotron 3 Super 120B | 1M | Standard |
| Claude Sonnet 4 | 200k | Standard |
| GPT-4o | 128k | Compact |

Custom models can be added via config.

## Safety Rules

1. **Auto-evolution is always gated** — no self-modification without human approval
2. **Context injection has hard limits** — never exceeds configured max tokens
3. **Goals are advisory** — internal goals suggest, never execute autonomously
4. **Meta-cognition is read-only** — assessment ≠ control
5. **Reflections are immutable** — once written, never edited (append-only)

## Pitfalls

- **Don't inflate context** — the #1 mistake is injecting too much state. Let the Context Manager decide.
- **Reflections must be compact** — a 30min reflection should produce ≤200 words of summary
- **World model drift** — beliefs can become stale. Set `maintenance` drive high to auto-prune.
- **Goal overload** — don't let the goal queue grow unbounded. Max 10 active goals.

## Status

🚧 **Early Alpha** — Architecture defined, core modules being implemented. Not yet production-ready.
