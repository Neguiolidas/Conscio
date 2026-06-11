# Changelog — Conscio

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
