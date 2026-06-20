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
┌─────────────── Structural cognition · conscio/structural* ────────────┐
│ StructuralDistiller (graph.json → ranked signal; data, never code/R10) │
│ budget-adaptive injection · consent (per-workspace) · drift+freshness  │
└────────────────────────────────────────────────────────────────────────┘
┌─────────────── Embodiment · conscio/mcp/ (propose-only) ──────────────┐
│ conscio-mcp: hand-rolled JSON-RPC 2.0 over stdio (stdlib only)         │
│ feed/note/advisory/recall/propose_action/propose_plan · never executes │
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

## Embodiment (MCP, v2.0)

`conscio/mcp/` exposes the engine to **any** MCP host (CLI, IDE, agent) over a
hand-rolled, stdlib-only JSON-RPC 2.0 stdio server (`conscio-mcp`). It adds a
third, **propose-only** surface alongside `reflect()` and `act()`: the host feeds
perception (`feed`/`note`), reads cognition (`advisory`/`recall` + resources), and
asks for an audited verdict on an intent (`propose_action`/`propose_plan`, which
compose the existing Actor/Skeptic and **never execute**). The transport is
bounded-at-source against hostile input and nothing opens a socket. Audited
execution over MCP (`act`) is deferred to v2.0.1.

## Design invariants

- **Zero-dependency core** — `numpy` + stdlib only. Packaging/doc tooling lives in
  optional extras and never enters the runtime import graph.
- **Local-first** — the only network the core may touch is the inference backend
  (localhost by default). No network tools in the registry (rule R7).
- **Everything is data, never code** — skills are plan templates, not executable
  behavior; the audited pipeline re-validates everything.
