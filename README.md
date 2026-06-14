# Conscio 🧠✨

**A self-awareness framework for AI agents** — context-aware memory,
introspection, goal generation, and an audited agency layer that lets a model
act on its own conclusions under hard safety gates.

> *"The first step toward consciousness is knowing what you are and what limits you."*

Conscio runs **local-first** and **zero-deps at the core** (`numpy` + `sqlite3`,
nothing else). It is designed to make small, local models punch far above their
size by giving them memory, self-judgment, and procedural skill — and to prove
that claim by measurement, not assertion.

- **Current release:** `v1.3.0` — "Ship" (`pip install conscio`; public plugin surface — adapters, sensors, tools; docs site; tag→PyPI release automation; 1015 tests, CI green, mypy a real gate)

---

## What Conscio does

- **Knows itself** — detects its model and context window, adapts its footprint.
- **Reflects continuously** — a passive inner-monologue loop that observes,
  assesses confidence, and summarizes (`engine.reflect()` — advisory, never acts).
- **Generates its own goals** — driven by curiosity, maintenance, and evolution.
- **Acts under audit** — an opt-in agency layer (`engine.act()`) that proposes,
  audits, risk-gates, and only then executes — with a human gate for anything risky.
- **Learns procedures** — successful audited plans become reusable skills
  (procedural memory), fed back to the actor as few-shot exemplars.
- **Judges its own quality** — confidence calibration, blind-spot detection,
  coherence/dissonance metrics, meta-reflection.
- **Stores & retrieves knowledge** — FTS5 BM25 dual-index with RRF merging;
  optional semantic recall.
- **Consolidates while idle** — a dream cycle that releases, prunes, reconciles,
  crystallizes, and distills.
- **Persists across sessions** — heartbeat/handoff continuity with on-demand injection.

`reflect()` is the **passive heart** and is never allowed to act. Everything that
can change the world lives behind `act()` and its safety gates. This separation
is non-negotiable (see [Safety Rules](#safety-rules-non-negotiable)).

---

## Context-aware modes

Conscio detects the model's context window and adapts how much "consciousness
state" it injects. The mode governs **injection budget only** — never whether
the framework runs.

| Mode | Context window | Injection budget | What's injected |
|---|---|---|---|
| **Minimal** | < 128k | ≤ 200 tokens | Off-context everything; on-demand retrieval |
| **Compact** | 128k–256k | ≤ 500 tokens | Summary + last reflection + top goals |
| **Standard** ⭐ | 256k+ | ≤ 1000 tokens | Full state; world subgraph; self-assessment |

⭐ **Standard (256k+) is the recommended operating class.** Conscio runs on
anything from **8k context up** — small windows simply get the Minimal budget.

---

## Architecture (v1.1.0)

```
┌──────────────────────────────────────────────────────────────────────┐
│                        ConsciousnessEngine                            │
│                  orchestrator · lifecycle · injection                 │
└──────────────────────────────────────────────────────────────────────┘
   │
   │  reflect()  ── passive, advisory, append-only ──────────────────────┐
   ▼                                                                      │
┌─────────────── Witness loop (v0.1) ───────────────────────────────────┐│
│ InnerMonologue · WorldModel · MetaCognition · GoalGenerator           ││
│ AutoEvolution · ContextManager · ModelRegistry                        ││
└────────────────────────────────────────────────────────────────────────┘│
┌─────────────── Substrate (v0.2) ──────────────────────────────────────┐ │
│ ContentStore (FTS5 BM25 + RRF) · EventBus (SHA-256 dedup)             │ │
│ FilterPipeline (sanitize/redact) · TokenTracker · Migrator            │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Continuity (v0.2.3) ───────────────────────────────────┐ │
│ SessionLifecycle (6-step handoff) · SessionRAG (optional, lazy)        │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Metabolism & self-judgment (v0.3–0.5) ─────────────────┐ │
│ MetabolicContext (VITAL/ACTIVE/FATIGUE/CRITICAL) · DreamCycle         │ │
│ entropy pruning · friction · meta-reflect · ShardEngine · layering    │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Coherence (v0.6–0.8) ──────────────────────────────────┐ │
│ CoherenceEngine (epistemic/reality/ontological/temporal)             │ │
│ semantic reconciliation (antonym axes) · voice & axis presets         │ │
└────────────────────────────────────────────────────────────────────────┘ │
                                                                            │
   act()  ── opt-in agency, audited, gated ◀────────────────────────────────┘
   ▼
┌─────────────── Agency · conscio/agency/ (v1.0–1.1, F1–F4) ────────────┐
│ InferenceAdapter (Mock/Ollama/llama.cpp/OpenAI-compat) · OutputGateway │
│ ToolRegistry (sandboxed, no network) · ActPipeline · ActionLedger      │
│ Skeptic (hostile audit) · TrustMatrix · CircuitBreaker (quarantine)    │
│ ProbeSuite/ModelProfile · GBNF compiler · GoalArbiter · AutonomyLoop   │
│ Meter/MeteredAdapter · SkillLibrary (procedural memory) · Bench        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Quick start

```python
from conscio import ConsciousnessEngine

# Passive consciousness — auto-detects model and mode
with ConsciousnessEngine(model_name="kimi-k2.6") as engine:
    result = engine.reflect(
        world_state="All systems operational",
        confidence=0.8,
        anomalies=["Unusual latency spike detected"],
    )

    # Compact state for context injection
    injection = engine.get_state_for_injection()

    # Query / update the world model
    engine.world.add_entity("server", "system", state="healthy")
    engine.world.query("server health")

    # Cross-session memory (ContentStore FTS5 + optional SessionRAG)
    hits = engine.recall("latency incidents")
```

### Opt-in agency (audited, propose-only by default)

```python
from conscio.agency import OllamaAdapter

engine.attach_adapter(OllamaAdapter(model="qwen3.5:0.8b"))

report = engine.act()                 # downstream of reflect(); proposes only (L1)
if report.status.value == "proposed":
    print(report.proposal.tool, report.proposal.args)
    engine.approve(report.ledger_id)  # the human gate executes it

# Capability-aware autonomy loop under a binding budget
engine.probe()                        # lazy, empirical capability measurement
engine.run(budget=...)                # L3 heartbeat: reflect → act → dream, gated
```

Autonomy is **earned and measured**, never assumed: `ProbeSuite` measures the
attached model, `TrustMatrix` grants L1/L2/L3 from real calibration and ledger
history, and the `CircuitBreaker` quarantines misbehaving goals. HIGH-risk
actions are *always* queued for a human (R6).

---

## Safety rules (non-negotiable)

1. **No autonomous self-modification** — evolution proposals require human approval.
2. **Context injection has hard limits** — never exceeds the mode budget.
3. **Goals never execute directly** — only through the audited `act()` pipeline:
   validated output contract + semantic audit (Skeptic) + risk gating + earned
   autonomy (TrustMatrix) + circuit breaker with per-goal quarantine and lockdown.
4. **Reflections are append-only** — never edited once written.
5. **Cannot modify its own safety rules** — no self-referential gate bypass.
6. **HIGH-risk actions always require human approval** — never auto-executed.
7. **No network in the tool registry** — the only network the core may touch is
   the InferenceAdapter (localhost by default); shell lives in the sibling
   `conscio-shell`, outside this repo.
8. **Every external effect goes through the ActionLedger** — append-only, auditable.

---

## Module reference

**Core / Witness (v0.1)** — `ConsciousnessEngine`, `ContextManager`,
`ModelRegistry` (`conscio/models.py`), `WorldModel`, `MetaCognition`,
`GoalGenerator`, `AutoEvolution`, `InnerMonologue`.

**Substrate (v0.2)** — `ContentStore` (FTS5 BM25 dual-index, RRF, 8 categories),
`EventBus` (SHA-256 dedup, priorities, expiration), `FilterPipeline`
(`conscio/output_filter.py` — StripAnsi/CollapseBlank/MaxLines/TruncateLines +
`DedupBlocks`/`SecretMask`), `TokenTracker`, `Migrator`.

**Continuity (v0.2.3)** — `SessionLifecycle` (extract → enrich → emit → index →
reflect → write; heartbeat <1.5KB + handoff), `SessionRAG` (optional, lazy,
Ollama `nomic-embed-text`, numpy cosine; graceful FTS5 fallback).

**Metabolism & self-judgment (v0.3–0.5)** — `MetabolicContext` (life-energy
tiers, advisory), `DreamCycle` (Release → Prune → Reconcile → Crystallize →
Distill), entropy pruning, friction, meta-reflect, `ShardEngine` (cognitive-mode
inference), content layering, trajectory vector.

**Coherence (v0.6–0.8)** — `CoherenceEngine` (recursive-coherence metric;
advisory `coherence:dissonance` event), semantic reconciliation via antonym axes
(`conscio/semantic.py`, packs in `conscio/presets/axes/`), self-prompting, voice
presets.

**Agency — `conscio/agency/` (v1.0–1.1)**

- *F1 "Spine"* — `InferenceAdapter` (Mock/Ollama/LM Studio/llama.cpp/OpenAI-compat,
  stdlib urllib), `OutputGateway` (tiered decoding), `ToolRegistry` (sandboxed,
  risk levels, no network), `ActPipeline`/`act()` (L1 PROPOSE), `ActionLedger`.
- *F2 "Immunity"* — `Skeptic` (hostile-auditor clean call; fail-closed),
  `TrustMatrix` (earned autonomy), `CircuitBreaker` (per-goal quarantine).
- *F3 "Volition"* — `ProbeSuite`/`ModelProfile` (5 empirical micro-probes,
  SQLite-cached, no hardcoded model table), embedded schema→GBNF compiler,
  `GoalArbiter` + `AutonomyLoop` (`engine.run(budget)`), `engine.probe()`,
  `Meter`/`MeteredAdapter`, the **bench** (`python -m conscio.bench`).
- *F4 "Procedural"* — `SkillLibrary` (procedural memory as **data**, not code;
  R1 intact), **Distill** (the dream's fifth sub-phase), tier-aware few-shot
  exemplars with outcome settling and a ≥50% teaching gate, skill curve in the
  bench (`--skills N`).

**Perception & plugins (v1.3)** — `conscio.perception` (`SensorAdapter`,
`PerceptionFrame`, `MockSensor`): write a sensor, and
`PerceptionFrame.to_world_state()` feeds `reflect()` unchanged. `conscio.plugins`
discovers third-party `InferenceAdapter`/`SensorAdapter`/tool plugins via entry
points (`conscio.adapters` / `conscio.sensors` / `conscio.tools`), resilient to a
broken plugin. `conscio.risk.Risk` is the shared safety-tier vocabulary.

---

## Extending Conscio

Three stable extension points, usable directly or published by a third party and
auto-discovered via entry points:

```python
from conscio.plugins import discover_adapters, discover_sensors, discover_tools
# or from the CLI:  conscio plugins
```

```toml
# in your own package's pyproject.toml
[project.entry-points."conscio.sensors"]
my-sensor = "my_pkg:MySensor"        # a conscio.perception.SensorAdapter
```

Runnable examples: `examples/custom_adapter.py`, `examples/host_guardian.py`,
`examples/agent_companion.py`. Full guide: the **docs site** (see below).

---

## Bench

```bash
# offline, deterministic (MockAdapter)
python -m conscio.bench --adapter mock

# real backends (local by default)
python -m conscio.bench --adapter ollama:qwen3.5:0.8b --cycles 20
python -m conscio.bench --adapter lmstudio:qwen3.5-0.8b --cycles 20
python -m conscio.bench --adapter llamacpp --cycles 20 --json report.json
python -m conscio.bench --adapter openai:qwen3@http://localhost:8000/v1

# skill-acquisition curve (per-bucket validity / success / skill count)
python -m conscio.bench --adapter mock --skills 20
python -m conscio.bench --adapter ollama:gemma4:e4b --skills 40 --dream-every 10
```

Reports: probe profile, decode tier, per-tier syntactic validity, Skeptic
catch-rate (deterministic vs semantic), latency p50, calibration. `--skills N`
reports the per-bucket validity/success/exemplars/skill-count curve. Baselines
in `docs/bench/`.

---

## Model registry

Known models ship with the registry; unknown models are detected by context
window (`detect()` accepts a `context_window` override) or inferred from the name.

| Model | Context | Mode |
|---|---|---|
| GLM 5.1 | 131k | Compact |
| Kimi K2.6 | 256k | Standard |
| MiniMax M2.7 | 260k | Standard |
| Step Flash 3.7 | 260k | Standard |
| Nemotron 3 Super 120B | 1M | Standard |
| Claude Sonnet 4 | 200k | Standard |
| Claude Opus 4 | 200k | Standard |
| GPT-4o | 128k | Compact |
| Llama 3.1 70B | 128k | Compact |
| Qwen 2.5 72B | 131k | Compact |

```python
from conscio import ModelRegistry
ModelRegistry.register("my-model", context_window=200_000)
```

---

## Installation

```bash
pip install conscio          # from PyPI

pip install -e ".[dev]"      # from source, with the dev toolchain
pip install "conscio[docs]"  # to build the docs site (mkdocs-material)
```

Requires Python ≥ 3.10. Core depends only on `numpy`; `sqlite3` is stdlib. The
wheel ships two console scripts — `conscio` (version/info/reflect/plugins/bench)
and `conscio-bench` — and is typed (PEP 561). `dev`/`docs` extras never enter the
runtime import graph.

Docs site: guides, public-API reference, the claims ledger, and the bench reports
(built with `mkdocs build --strict`; see `docs/`).

---

## Testing

```bash
# Full suite (1015 tests) — house rule: one file per pytest process
# (low-RAM machines OOM on the full run; CI does the same)
for f in tests/test_*.py; do pytest "$f" -q; done

# Specific module
pytest tests/test_consciousness.py -v
pytest tests/test_agency_act.py -v
pytest tests/test_session_lifecycle.py -v
```

---

## Database

SQLite, WAL mode, default `~/.conscio/data/`:

```
conscio.db          # ContentStore + EventBus + ActionLedger + skills
token_tracker.db    # TokenTracker
meta_cognition.db   # MetaCognition
```

**Always** call `engine.close()` or use the `with` statement so WAL checkpoints flush.

---

## Session continuity

Seven layers of persistence (memory → agent config → skills → handoff → diary →
session DB/RAG → git). Configure your agent's hook to fire on `session:end` /
`session:reset`; Conscio runs a 6-step pipeline and writes:

- `<handoff_dir>/_latest_heartbeat.md` — compact (<1.5KB), auto-injected next session
- `<handoff_dir>/_session_handoff.md` — richer manual reference
- `<handoff_dir>/heartbeat_YYYYMMDD_HHMM.md` — dated archive

---

## Audit history

- **v1.3.0 — "Ship"** — Conscio becomes installable and extensible: `pip install
  conscio` (single-source version, console scripts `conscio`/`conscio-bench`, PEP
  561 typed, wheel+sdist pass `twine check`, core pulls only numpy). A public
  plugin surface — `InferenceAdapter`, the new `SensorAdapter` perception
  interface (`conscio.perception`; feeds `reflect()` untouched), and tools —
  discoverable via entry points and resilient to a broken plugin
  (`conscio.plugins`). MkDocs Material docs site (`mkdocs build --strict`).
  Release automation: tag→PyPI via OIDC trusted publishing, docs→Pages, CI build
  smoke. Examples gallery (custom-adapter, host-guardian, agent-companion). `Risk`
  unified into `conscio.risk` (re-exported; no behavior change). reflect()
  untouched, zero-deps core intact. +31 tests. **1015 total.**
- **v1.2.0 — "Prove"** — the central claim turns from machinery (Mock) into
  measurement: on `qwen3.5-0.8b` (LM Studio, CPU) execution success rose
  0.2 → 1.0 once Distill served past successes as few-shot, and the Skeptic's
  semantic catch-rate was 1.0 (`docs/bench/v1.2-skill-curve.md`,
  `docs/CLAIMS.md`). F2-deferred debt closed (empty-value validation, `fs_read`
  cap, error sanitization, `HTTPError` mapping, ledger `busy_timeout`, atomic
  `approve()` claim, lockdown-persistence e2e). Bench hardened for real backends
  (clean backend-down exit, crash-safe incremental curve). LM Studio backend
  added. reflect() untouched, zero-deps intact. +21 tests. **984 total.**
- **v1.1.0 — F4 "Procedural"** — procedural memory closes the competence loop:
  `SkillLibrary` (skills distilled from successful ledger plans; data, not code —
  R1 intact), Distill as the dream's fifth sub-phase (watermarked, last on
  purpose), tier-aware few-shot exemplars with outcome settling and a 50%
  teaching gate, skill-acquisition curve in the bench (`--skills N`), reactive
  MockAdapter. Debt paid: deprecated `datetime.utcnow()` removed repo-wide, CI
  runs tests one file at a time, mypy is a real gate, public `engine.state`.
  reflect() untouched. +48 tests. **963 total.**
- **v1.0.0 — F3 "Volition"** — the loop closes: ProbeSuite/ModelProfile
  (empirical, SQLite-cached, no hardcoded model table), schema→GBNF compiler,
  GoalArbiter, `engine.run(budget)` L3 heartbeat with binding ActBudget +
  metabolic gating, `engine.probe()`, earned L3 autonomy, Meter/MeteredAdapter,
  the bench CLI. +70 tests.
- **v1.0.0b1 — F2 "Immunity"** — semantic immune system: Skeptic, TrustMatrix,
  per-goal quarantine, risk gating, mixed-cortex audits, approval queue. 20-proposal
  adversarial suite: 100% deterministic sabotage blocked, zero executions.
- **v1.0.0a1 — F1 "Spine"** — the agency subpackage lands: contracts + zero-dep
  validator, InferenceAdapter (Mock/Ollama/llama.cpp/OpenAI-compat), OutputGateway,
  sandboxed ToolRegistry, append-only ActionLedger, minimal CircuitBreaker,
  `engine.act()` L1 PROPOSE. Safety rules amended (R3 rewritten; R6–R8 added). +83 tests.
- **v0.8.0 — Semantic Reconciliation** — contradiction detection via embedding
  antonym axes, off the hot path in the dream Reconcile sub-phase; opt-in
  non-destructive `SemanticDedup`. 56 tests. 600 total.
- **v0.7.0 — Recursive Coherence** — coherence→action loop: advisory
  `DreamRecommendation`, pure self-prompting (one bounded goal/cycle). 23 tests.
- **v0.6.0 — Coherence** — `CoherenceEngine` (epistemic/reality/ontological/
  temporal), static voice presets. 46 tests.
- **v0.5.0 — Cognitive Modes** — ShardEngine, trajectory vector, content layering. 37 tests.
- **v0.4.0 — Self-Judgment** — entropy pruning, friction, meta-reflect. 24 tests.
- **v0.3.0 — Metabolic Consciousness** — MetabolicContext + DreamCycle,
  `engine.recall()` cross-session memory, OutputFilter `DedupBlocks`+`SecretMask`. 68 tests.
- **v0.2.3 — Session lifecycle** — 6-step handoff pipeline; `session` type/category. 31 tests.
- **v0.2.0–0.2.2** — integration audits, session handoff, on-demand heartbeat injection.
- **v0.1.0 (2026-06-03)** — initial release. 313 tests.

---

## License

MIT — Neguiolidas / Neguitech
