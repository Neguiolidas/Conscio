# Changelog — Conscio

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.1] — 2025-06-05

### Fixed

- **OutputFilter pipeline config keys in `engine.py`** — `build_pipeline_from_dict` was called
  with incorrect dict keys (`"max"` instead of `"max_lines"`, `"max_chars"` instead of `"max_width"`).
  This caused MaxLines to run at default 100 lines (intended: 200) and TruncateLines at default
  200 chars (intended: 8000). Silently degraded output quality without crashing.
- **ConsciousnessEngine missing lifecycle cleanup** — No `close()`, `__enter__`/`__exit__` methods,
  meaning SQLite-backed modules (ContentStore, EventBus, TokenTracker) were never properly closed.
  WAL files would grow indefinitely without checkpoint. Added `close()` (idempotent, safe),
  `__enter__`/`__exit__` for context manager usage.
- **Dead import in `reflect.py`** — `build_pipeline_from_dict` was imported but never used.
  Removed to keep imports clean.

### Added

- **3 regression tests** in `TestV02Regressions` class:
  - `test_output_filter_pipeline_config_keys` — verifies correct config dict keys
  - `test_engine_close_and_context_manager` — verifies `close()` is safe and idempotent
  - `test_engine_context_manager` — verifies `with` statement properly manages resources

### Audit

- Full audit of 14 modules (~5400 lines) and 6 test files (~3000 lines)
- No additional bugs found in ContentStore, EventBus, OutputFilter, TokenTracker, Migrator,
  WorldModel, MetaCognition, GoalGenerator, AutoEvolution, InnerMonologue, ContextManager, or Models
- Test count: 313 → 316 (all passing)

---

## [0.2.0] — 2025-06-04

### Added

- **ContentStore** — FTS5 BM25 dual-index knowledge base (porter+unicode61 + trigram tokenizers)
  with Reciprocal Rank Fusion (RRF) merging. Categories: reflection, perception, trading, system,
  error, consciousness, external.
- **EventBus** — Deduplicated event bus using SHA-256 content hashing. Supports priorities
  (low/normal/high/critical) and categories (system, trading, consciousness, external).
  Expired events are auto-pruned.
- **OutputFilter** — 8-stage text compression pipeline: StripAnsi → CollapseBlank → MaxLines →
  TruncateLines. Configurable via `build_pipeline_from_dict()`.
- **TokenTracker** — Token estimation (chars/4) with per-source tracking and savings reporting.
  Sources: reflection, perception, injection, trading, system, consciousness, tool_output, external.
- **Migrator** — JSON → SQLite migration tool for upgrading from v0.1 storage format.
- **Engine v0.2 integration** — ConsciousnessEngine now initializes and uses all v0.2 modules
  in the `reflect()` pipeline: EventBus event emission, ContentStore indexing, OutputFilter
  compression, TokenTracker recording.
- **reflect.py v0.2 integration** — Active perception script uses EventBus, ContentStore,
  TokenTracker alongside the engine.

### Changed

- `CONFLUENCE_THRESHOLD` raised from 0 to 30 (prevents noise trades in OrionTrading)
- SL/TP validation changed from 10% to 5% max distance from entry
- R:R gate added — trades with risk:reward < 1.0 are automatically rejected

---

## [0.1.0] — 2025-06-03

### Added

- **ConsciousnessEngine** — central orchestrator
- **ContextManager** — mode detection and context budget allocation
- **ModelRegistry** — model → context → mode mapping
- **WorldModel** — entity/relation store with predictions
- **MetaCognition** — confidence tracking, blind spot detection, error patterns
- **GoalGenerator** — drive-based goal generation (curiosity, maintenance, evolution)
  with meta-score computation
- **AutoEvolution** — safe self-modification proposals with human approval gates
- **InnerMonologue** — reflection/observe/summarize loop
- **WorldModel decay** — temporal relevance decay (exp(-λt)), query-based relevance boost,
  pruning of irrelevant entities
- **MetaCognition → GoalGenerator connection** — blind spots generate evolution goals,
  frequent errors generate maintenance goals, low confidence boosts evolution drive
- **Goal meta-score** — computed from priority, confidence, and calibration
- **AutoEvolution observer** — `observe_errors()` creates proposals from recurring errors
  with deduplication
- 313 tests across 6 test files
