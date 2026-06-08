# Changelog

All notable changes to Conscio will be documented in this file.

## [0.9.0] — 2026-06-08

### Added
- **Test Coverage + Wiring**: 100 new tests (613 → 713), 14 previously untested methods covered
- **ContentLayerManager**: unified recall across FTS5, SessionRAG, and WorldModel
- **SessionLifecycle class**: clean API for session events with callback registration
- **OutputFilter integration**: heartbeat/handoff filtering in record_session_lifecycle()
- **Configurable handoff**: `session_db` and `handoff_dir` params on `record_session_lifecycle()` and `SessionLifecycle`
- **Environment variables**: `CONSCIO_SESSION_DB`, `CONSCIO_HANDOFF_DIR` for custom paths
- **Handoff disable**: `handoff_dir=None` skips file writes (pipeline still runs)

### Fixed
- **Security audit**: no exploitable vulnerabilities (parameterized SQL, no eval/exec with user input)
- **Performance P0**: ContentStore.compact() N+1 → batch delete (3 DELETEs + 1 commit)
- **Performance P0**: ContentStore._rrf_merge N+1 → single WHERE rowid IN(...) query
- **Performance P1**: np.frombuffer crash on corrupt embeddings → try/except ValueError
- **Bug**: UnboundLocalError in session_lifecycle.py (heartbeat/handoff before try/finally scope)
- **Bug**: Module-level MetabolicContext import moved to top of engine.py
- **Bug**: Fragile __import__ lambda replaced with proper SessionRAG factory
- **Bug**: 12 bare `except Exception: pass` blocks now log with context
- **Bug**: ContentLayerManager.recall() validates k parameter (1 ≤ k ≤ 50)
- **Dependency**: numpy>=1.24 added to pyproject.toml (was missing)

## [0.8.0] — 2026-06-07

### Added
- **SemanticEngine**: antonym-axis polarity via pole projection
- **ContradictionDetector**: lexical fast-path → semantic axis opposition
- **SemanticDedup**: opt-in output filter stage (annotate near-dup blocks)
- **AxisPack**: antonym-axis presets (core.json)
- **WorldModel accessors**: public relation/count reads + state_log + contradiction cache
- **Dream Reconcile**: semantic contradiction marking in dream cycle

## [0.7.0] — 2026-06-07

### Added
- **Dream+Coherence loop**: DreamCycle feeds coherence state; coherence drives dream priority
- **Self-Prompting**: generates self-directed prompts based on coherence gaps + goals

## [0.6.0] — 2026-06-06

### Added
- **CoherenceEngine**: recursive-coherence state metric (alignment × convergence × depth)
- **Voice Presets**: YAML voice presets system with `resolve_voice_preset()`

## [0.5.0] — 2026-06-06

### Added
- **ShardEngine**: 7 cognitive modes (ARCHITECT→DREAMER), keyword-based inference
- **Trajectory Vector**: trajectory, vibes, identity_anchor soft fields on SessionSummary
- **ContentLayerManager**: ROUTINE/PROCESSING/INTUITION enum, `layer_of()`, `layer_sort_key()`

## [0.4.0] — 2026-06-05

### Added
- **Entropy scoring**: world model entity entropy (age + isolation + relation count)
- **prune_by_entropy()**: opt-in entropy-based pruning (coexists with prune_stale)
- **Friction**: deferred crystallization for recently changed entities
- **Meta-reflect**: meta_confidence labels (HIGH/MEDIUM/LOW) on EventBus reflection events

## [0.3.0] — 2026-06-05

### Added
- **DreamCycle**: 3-phase maintenance (release → prune → crystallize)
- **MetabolicContext**: 4-tier system (VITAL/ACTIVE/FATIGUE/CRITICAL)
- **SessionRAG**: semantic search over sessions (Ollama optional, graceful degradation)
- **engine.recall()**: cross-session retrieval (FTS5 primary + SessionRAG fallback)
- **OutputFilter stages**: DedupBlocks + SecretMask

## [0.2.0] — 2026-06-04

### Added
- **ContentStore**: FTS5 BM25 dual-index knowledge base with RRF merge
- **EventBus**: session event tracking with dedup by hash, priority, compaction
- **OutputFilter**: 8-stage YAML-driven filter pipeline
- **TokenTracker**: char/4 token estimation, savings tracking, budget dashboard

## [0.1.0] — 2026-06-03

### Added
- Core consciousness framework: ModelRegistry, ContextManager, InnerMonologue, WorldModel, MetaCognition, GoalGenerator, AutoEvolution, ConsciousnessEngine
- FTS5-backed reflection storage
- Budget-aware context injection (Minimal/Compact/Standard modes)
