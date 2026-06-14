# Architecture

`ConsciousnessEngine` is the orchestrator. Everything below it is layered, and the
two surfaces — passive `reflect()` and audited `act()` — never blur.

```
┌──────────────────────────────────────────────────────────────────────┐
│                        ConsciousnessEngine                            │
│                  orchestrator · lifecycle · injection                 │
└──────────────────────────────────────────────────────────────────────┘
   │
   │  reflect()  ── passive, advisory, append-only ──────────────────────┐
   ▼                                                                      │
┌─────────────── Witness loop ──────────────────────────────────────────┐│
│ InnerMonologue · WorldModel · MetaCognition · GoalGenerator           ││
│ AutoEvolution · ContextManager · ModelRegistry                        ││
└────────────────────────────────────────────────────────────────────────┘│
┌─────────────── Substrate ─────────────────────────────────────────────┐ │
│ ContentStore (FTS5 BM25 + RRF) · EventBus (SHA-256 dedup)             │ │
│ FilterPipeline (sanitize/redact) · TokenTracker · Migrator            │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Continuity ────────────────────────────────────────────┐ │
│ SessionLifecycle (6-step handoff) · SessionRAG (optional, lazy)        │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Metabolism & self-judgment ────────────────────────────┐ │
│ MetabolicContext (VITAL/ACTIVE/FATIGUE/CRITICAL) · DreamCycle         │ │
│ entropy pruning · friction · meta-reflect · ShardEngine · layering    │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Coherence ─────────────────────────────────────────────┐ │
│ CoherenceEngine (epistemic/reality/ontological/temporal)             │ │
│ semantic reconciliation (antonym axes) · voice & axis presets         │ │
└────────────────────────────────────────────────────────────────────────┘ │
                                                                            │
   act()  ── opt-in agency, audited, gated ◀────────────────────────────────┘
   ▼
┌─────────────── Agency · conscio/agency/ ──────────────────────────────┐
│ InferenceAdapter (Mock/Ollama/llama.cpp/OpenAI-compat) · OutputGateway │
│ ToolRegistry (sandboxed, no network) · ActPipeline · ActionLedger      │
│ Skeptic (hostile audit) · TrustMatrix · CircuitBreaker (quarantine)    │
│ ProbeSuite/ModelProfile · GBNF compiler · GoalArbiter · AutonomyLoop   │
│ Meter/MeteredAdapter · SkillLibrary (procedural memory) · Bench        │
└────────────────────────────────────────────────────────────────────────┘
```

## The two surfaces

- **`reflect()`** runs the Witness loop over the substrate. It is passive,
  advisory, append-only, and makes no LLM calls. It returns a state dict and a
  compact injection string. This contract is fixed and never broken.
- **`act()`** lives entirely in `conscio/agency/`. It is opt-in (you must
  `attach_adapter`), proposes one action at a time, and gates every action behind
  a validated contract, a hostile Skeptic audit, risk tiers, earned trust, and a
  circuit breaker. See [Safety rules](safety-rules.md).

## Perception

A `SensorAdapter` produces a `PerceptionFrame`; `frame.to_world_state()` yields
the plain string `reflect()` consumes. Perception is therefore pluggable *without
modifying `reflect()`* — the same move that made inference and action pluggable.

## Design invariants

- **Zero-dependency core** — `numpy` + stdlib only. Packaging/doc tooling lives in
  optional extras and never enters the runtime import graph.
- **Local-first** — the only network the core may touch is the inference backend
  (localhost by default). No network tools in the registry (rule R7).
- **Everything is data, never code** — skills are plan templates, not executable
  behavior; the audited pipeline re-validates everything.
