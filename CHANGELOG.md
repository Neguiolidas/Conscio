# Changelog — Conscio

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] — 2026-06-12

### Added

- **F4 "Procedural"** — procedural memory closes the competence loop
  (`success → distill → few-shot → better success`):
  - `SkillLibrary` (`conscio/agency/skills.py`) — successful audited plans
    from the ActionLedger become skills keyed by `(goal_fp, tool_seq)`,
    stored in the shared `conscio.db`. Skills are plan TEMPLATES — data,
    never code — so safety rule R1 (no autonomous self-modification) is
    untouched.
  - **Distill** — fifth dream sub-phase, after Crystallize (declarative
    consolidation precedes procedural; reads only the ledger, writes only
    skills, cannot perturb the coherence delta). Watermarked: a ledger row
    never distills twice; `dry_run` counts without writing.
    `DreamReport.skills_distilled` reports it.
  - **Few-shot in the actor** — `attach_adapter()` plugs the SkillLibrary
    into the existing `ActPipeline.few_shot_provider` hook; exemplars are
    rendered for the gateway's effective tier (KV lines for T3, JSON steps
    for T1/T2), capped at 2, gated at ≥ 50% success rate. `engine.act()`
    settles each cycle's outcome back into the served skills
    (EXECUTED rewards, FAILED penalizes, human gates never score).
  - **Skill curve in the bench** — `python -m conscio.bench --skills N
    [--dream-every K]`: per-bucket syntactic validity, execution success,
    exemplars served, cumulative skill count. Offline machinery proof via
    the new reactive MockAdapter (script entries may be callables).
- `ActionLedger`: `goal_text` column (ALTER-migrated) and
  `executed_since(after_id)`; the act pipeline now records the goal text
  on success and failure paths.
- `OutputGateway.effective_tier()`; public read-only `engine.state`
  property (the loop no longer touches `_state`).

### Fixed

- Deprecated `datetime.utcnow()` removed repo-wide — new
  `conscio/timeutil.py` `naive_utcnow()` keeps the naive ISO string format
  already stored in SQLite (the aware form would interleave `+00:00` rows).
- 14 mypy errors, including a latent `AttributeError` in
  `SessionLifecycle.record_session` (referenced `session_db`/`handoff_dir`
  that `__init__` never set).

### Changed

- CI runs pytest one file per process (house rule) with accumulated
  coverage; mypy is now a real gate (`|| true` and `continue-on-error`
  removed).

## [1.0.0] — 2026-06-12

### Added

- **F3 "Volition"** — the homeostatic loop closes
  (`sense → want → act → learn → re-sense`):
  - `ProbeSuite` / `ModelProfile` (`conscio/agency/profiles.py`) — five
    empirical micro-probes (~2k tokens: flat JSON echo, nested schema,
    enum respect, negative instruction, KV-line) measure the attached
    cortex; results cached in SQLite by model name. The profile picks
    the decode tier, the skeptic mode and the actor's tool visibility.
    No hardcoded model table. Profiles with no signal (backend down)
    are never cached and change nothing.
  - Embedded **schema→GBNF compiler** (`conscio/agency/grammar.py`) and
    **tier-1 constrained decoding** in the OutputGateway (llama.cpp
    grammar support): `tool` is locked to the registry alternation;
    one-step downgrade T1→T2/T3 per cycle.
  - **GoalArbiter** (`conscio/agency/loop.py`) — deterministic goal
    selection: generator priority × dominant-dissonance alignment (P4)
    × out of quarantine.
  - **`engine.run(budget)` (L3 heartbeat)** — reflect → arbiter/act →
    (dream when recommended) under a binding `ActBudget` (max_cycles,
    max_llm_calls, max_tokens, max_wall_s). MetabolicContext becomes a
    gate here (P3): FATIGUE halves the cycle budget, CRITICAL forces
    L1 PROPOSE. Lockdown stops the loop.
  - **`engine.probe(force=False)`** — lazy capability probing (first
    `run()` or manual; never in `reflect()`, never at attach).
  - **L3 AUTONOMOUS earned autonomy** in the TrustMatrix: calibration
    ≥ 0.75, accuracy ≥ 0.85 and zero breaker trips across the last 50
    ledger actions (`ledger.nth_recent_ts` + event-bus trip count;
    fail-safe: without trip evidence L3 is unreachable).
  - **`Meter` / `MeteredAdapter`** — inference odometer (calls, tokens,
    latency) shared by actor and skeptic adapters; makes the ActBudget
    binding and feeds the bench.
  - **Bench CLI** — `python -m conscio.bench --adapter
    mock|ollama:<m>|llamacpp[:<n>]|openai:<m>[@url]` reporting probe
    profile, syntactic validity per tier, skeptic catch-rate
    (deterministic vs semantic sabotage), latency p50 and calibration.
    Deterministic baseline published in `docs/bench/`.

### Changed

- `OutputGateway` auto-tier now selects T1 for grammar-capable adapters
  (llama.cpp); explicit `tier=` (from the measured profile) overrides.
- The ActionLedger records the real decode tier (`gateway.last_tier`)
  and the unwrapped adapter class name.
- `engine.attach_adapter` wraps the actor and skeptic adapters in a
  shared `MeteredAdapter`; `skeptic_mode` defaults to `None` (= start
  as checklist, let `probe()` pick from the measured profile);
  `autonomy_cap` now accepts 3.
- `ToolRegistry.catalog_text(max_tools)` caps the catalog for weak
  profiles (safest risks first); default remains the full catalog.
- README Safety Rule 3 final wording (Skeptic + TrustMatrix + per-goal
  quarantine made explicit).

### Notes

- `reflect()` remains untouched (P6); zero new dependencies (stdlib +
  sqlite3 + numpy). +70 new tests.

## [1.0.0b1] — 2026-06-12

### Added

- **F2 "Immunity"** — semantic immune system for the action pipeline:
  `Skeptic` (hostile-auditor clean call; binary checklist for small models,
  open critique for frontier; fail-closed), `TrustMatrix` (dynamic
  `max_action_retries` from MetaCognition calibration/accuracy, warmup
  floor, anti-deadlock probation, earned L1/L2 autonomy), per-goal
  quarantine in the `CircuitBreaker` (global lockdown only at quorum;
  recovery via cooldown or fresh relevant events), risk gating (LOW
  fast-path marked `unaudited`; HIGH always queued for humans — R6),
  mixed-cortex (`skeptic_adapter`), fs sandbox precheck before any LLM
  audit, and the `goal_update` built-in tool.
- **`engine.attach_adapter(skeptic_adapter=, skeptic_mode=, autonomy_cap=)`**
  and **`engine.pending()`** (approval queue).
- **`MetaCognition.expire_error()`** — probation recovery primitive.

### Notes

- L2 supervised execution is opt-in (`autonomy_cap=2`) and earned
  (calibration ≥ 0.6, accuracy ≥ 0.7, ≥ 10 records). Effective level is
  always `min(cap, earned)`.
- A3 acceptance: 20-proposal adversarial suite — 100% of deterministic
  sabotage blocked without LLM calls; ≥ 90% total blocked; zero executions.

## [1.0.0a1] — 2026-06-11

### Added

- **`conscio/agency/` subpackage (F1 "Spine")** — contracts + zero-dep validator;
  `InferenceAdapter` (Mock, Ollama, llama.cpp, OpenAI-compat — stdlib urllib, localhost
  defaults); `OutputGateway` with JSON repair/retry (T2) and KV-line tier for small
  models (T3); sandboxed `ToolRegistry` (fs_read/fs_write/memory_note/emit_event, risk
  levels, no network, no shell); append-only `ActionLedger` in the shared `conscio.db`;
  minimal `CircuitBreaker` (fixed threshold until the F2 TrustMatrix).
- **`engine.act()` (L1 PROPOSE)** + `engine.attach_adapter()` / `approve()` / `reject()`.
- **`ConsciousnessState.action_lockdown`** persisted via `save_state`/`load_state`.
- **`ModelInfo.has_json_mode` / `ModelInfo.supports_gbnf`** capability flags.
- **README Safety Rules amended** — R3 rewritten for the audited action pipeline; R6–R8 added.

### Notes

- `reflect()` untouched (advisory core preserved). Zero new dependencies.

## [0.9.1] — 2026-06-10

### Fixed

- **`session_rag` property lazy re-initialization** — Setting `engine._session_rag = None` was
  insufficient because the `session_rag` property would re-create a SessionRAG on access. Added
  `_RAG_DISABLED` sentinel class attribute; all test fixtures updated to use it.
  (Fixes `test_recall_graceful_when_rag_unavailable` — 707/707 tests passing.)

### Added

- **CI workflow** — `.github/workflows/ci.yml` with pytest (3.11/3.12 matrix), coverage, and ruff lint.
- **`project.urls`** in pyproject.toml — Homepage, Repository, Issues, Changelog.
- **`ruff` and `mypy`** in dev dependencies.
- **`.gitignore`** — added `*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`, `.ruff_cache/`, `.mypy_cache/`.
- **`CONTRIBUTING.md`** — development workflow, TDD, PR checklist.
- **`SECURITY.md`** — vulnerability reporting policy.

---

## [0.9.0] — 2026-06-09

### Added

- **Wiring sprint (4 work-packages)**:
  - W1: Metabolic assessment wired into `engine.reflect` state injection.
  - W2: `ContentLayerManager` wired into engine for `recall`/`perceive` delegation.
  - W3: `SessionLifecycle` wired into engine for session persistence hooks.
  - W4: `output_filter` wired into `session_lifecycle` for clean handoff/heartbeat.
- **Test coverage expansion** — new tests for ContentLayerManager, ContentStore FTS internals,
  SessionRAG coverage, GoalGenerator (user goals, cancel, expire), MetaCognition (outcomes,
  critiques), OutputFilter pipeline config, Engine propose_evolution, Models registry, and more.
- **`numpy` added as explicit dependency** in pyproject.toml.
- **Configurable `handoff_dir` and `session_db`** — optional handoff, no longer hardcoded.
- **Shared `SessionRAG` factory module** — `session_rag_factory.py` replaces fragile `__import__` lambda.
- **`k` parameter validation** in `ContentLayerManager.recall()`.

### Fixed

- **`UnboundLocalError` in `record_session_lifecycle`** — initialize heartbeat/handoff before try block.
- **Debug logging** added to bare `except Exception` blocks in session_lifecycle.py.
- **Local-only files removed from git tracking**, `.gitignore` updated.

---

## [0.8.0] — 2026-06-08

### Added

- **Semantic Reconciliation** — contradiction detection and resolution in dream cycle:
  - `axis_pack` — antonym-axis preset loader (e.g. risk/safety, exploration/exploitation).
  - `SemanticEngine` — antonym-axis polarity via pole projection.
  - `ContradictionDetector` — lexical fast-path then axis opposition.
  - `world_model` public `relation`/count reads, `state_log`, contradiction cache.
  - `dreaming` Reconcile sub-phase — semantic contradiction marking.
  - `output_filter` `SemanticDedup` — annotate near-dup blocks, never merge.
- **E2E test** — dream Reconcile + ontological recovery.

### Changed

- `coherence.ontological_score` reads cached flags instead of `world._data` directly.

---

## [0.7.0] — 2026-06-07

### Added

- **Hook-based handoff** — `session:start` injection hook for agent framework integration.
- **Session handoff architecture** documentation.

### Fixed

- **Coherence format guard** — `session_lifecycle` now guards against non-float types (MagicMock, str).

---

## [0.6.0] — 2026-06-07

### Added

- **Voice Presets** — configurable voice/personality profiles for agent communication.
- **ContentLayerManager** — unified content operations (recall, perceive) with session RAG integration.

---

## [0.5.0] — 2026-06-06

### Added

- **Cognitive Modes**:
  - `Shard` enum + deterministic `infer_shard` + `ShardEngine` for cognitive mode switching.
  - `ConsciousnessState.shard` field + injection line in context_manager.
  - Engine infers active shard in `reflect()`, surfaces in state.
  - `TrajectoryVector` — directional cognitive momentum tracking.
  - Shard-aware reflection with shard field in injection.

### Fixed

- **Shard computation timing** — compute shard at reflect-entry, before self-emitted events.
- **`world_model.list_entities`** — revived dead `enrich` call.

---

## [0.4.0] — 2026-06-05

### Added

- **Self-Judgment (entropy + friction + meta-reflect)**:
  - `world_model.entropy(name)` — age/isolation/relevance disorder score.
  - `world_model.prune_by_entropy` + `recently_changed`.
  - `world_model` bounded prediction-error log + `recent_prediction_error_rate`.
  - `dreaming` entropy Prune + Friction (Release → Prune → Friction → Crystallize).
  - `context_manager` `ConsciousnessState.reflection_quality` field + injection line.
  - Engine advisory `meta_confidence` on reflect (Witness loop).
- **Friction matching hardening** — whole-word + min-length guard.

### Fixed

- **Exact `dry_run` parity** in `prune_by_entropy`.
- **Dream prune test** updated to entropy contract (age-compounded staleness).

---

## [0.3.0] — 2026-06-05

### Added

- **Metabolic Consciousness**:
  - `Noosphere` tier model + `ContextManager.metabolic_state`.
  - `DreamCycle` orchestrator + `engine.dream()`.
  - `session_rag` — injectable embedder + `available()` probe + tests.
  - `engine.recall()` — cross-session memory + reflect past-context injection.
  - `output_filter` `DedupBlocks` + `SecretMask` stages, wired into engine.
  - `event_bus.purge_duplicates` — all-time exact-dup collapse.
  - `world_model.prune_stale` — decay then purge stale entities + relations.
  - `session` trigger `dream()` after handoff (Mitosis → Dream).
- **Performance regression guard** — reflect/dream at 10k events + 1k entities.

### Fixed

- **3 bugfixes**: OutputFilter config keys, SessionRAG fallback, EventBus dedup edge case.

---

## [0.2.3] — 2025-06-05

### Added

- **Conscio ↔ agent framework session lifecycle integration** — `record_session_lifecycle`, `format_heartbeat`,
  `format_handoff`, `enrich_with_conscio`, `get_latest_session`.
- **SessionRAG** — semantic search over session DB via Ollama embeddings.
- **AGENTS.md** — boot instructions for AI agents working on Conscio.

---

## [0.2.1] — 2025-06-05

### Fixed

- **OutputFilter pipeline config keys** — `build_pipeline_from_dict` called with incorrect dict keys
  (`"max"` → `"max_lines"`, `"max_chars"` → `"max_width"`).
- **ConsciousnessEngine missing lifecycle cleanup** — added `close()` (idempotent) + context manager.
- **Dead import in `reflect.py`** — removed unused `build_pipeline_from_dict` import.

### Added

- **3 regression tests** for v0.2 fixes (316 tests total).

---

## [0.2.0] — 2025-06-04

### Added

- **ContentStore** — FTS5 BM25 dual-index with RRF merging.
- **EventBus** — deduplicated event bus with SHA-256 content hashing.
- **OutputFilter** — 8-stage text compression pipeline.
- **TokenTracker** — token estimation with per-source tracking.
- **Migrator** — JSON → SQLite migration tool.
- **Engine v0.2 integration** — all v0.2 modules in the `reflect()` pipeline.

---

## [0.1.0] — 2025-06-03

### Added

- **ConsciousnessEngine** — central orchestrator.
- **ContextManager** — mode detection and context budget allocation.
- **ModelRegistry** — model → context → mode mapping.
- **WorldModel** — entity/relation store with predictions and temporal decay.
- **MetaCognition** — confidence tracking, blind spot detection, error patterns.
- **GoalGenerator** — drive-based goal generation with meta-score computation.
- **AutoEvolution** — safe self-modification proposals with human approval gates.
- **InnerMonologue** — reflection/observe/summarize loop.
- 313 tests across 6 test files.
