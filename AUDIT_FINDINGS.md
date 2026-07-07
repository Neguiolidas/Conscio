# Conscio Deep Audit Findings

Audited: all `.py` files under `conscio/` (source, not tests).
Date: 2026-07-07

---

## 1. Hardcoded Model/Provider Strings (inconsistent across files)

### CRITICAL — Model name mismatches between registry and adapters/hub

- **`conscio/agency/adapters.py:233`** — `AnthropicAdapter.__init__` defaults `model="claude-sonnet-4-6"`. The ModelRegistry (`models.py:100`) has `"claude-sonnet-4"` (no `-6` suffix). A user relying on the adapter default gets a model name the registry cannot resolve → heuristic fallback to 128k.

- **`conscio/hub/providers.py:22`** — `KNOWN_MODELS["anthropic"]` = `["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"]`. None of these exist in `ModelRegistry._known_models`. The registry has `claude-sonnet-4` and `claude-opus-4` only. `claude-haiku-4-5` has no registry entry at all.

- **`conscio/agency/adapters.py:196`** — `OpenAIAdapter.__init__` defaults `model="gpt-4o"`. This one IS consistent with the registry (`models.py:105`).

- **`conscio/agency/adapters.py:284`** — `GeminiAdapter.__init__` defaults `model="gemini-2.5-pro"`. Consistent with registry.

### Hardcoded model in docstring/example

- **`conscio/engine.py:118`** — Docstring example: `ConsciousnessEngine(model_name=os.environ.get("CONSCIO_MODEL", "glm-5.1"))`. This contradicts `resolve_model_name()` (`models.py:640`) which raises `ValueError` when no model is specified. The example silently suggests `glm-5.1` as a default, but the actual code path (`cli.py:24`, `daemon.py:415`, `mcp/server.py:544`) uses `""` and requires explicit specification. Stale/misleading docstring.

- **`conscio/bench.py:128`** — `OllamaAdapter(model=arg or "hermes3")`. Hardcoded `"hermes3"` as a default Ollama model name. Should be a constant or require explicit specification.

- **`conscio/session_rag.py:48`** — `DEFAULT_EMBED_MODEL = "text-embedding-nomic-embed-text-v1.5"`. Hardcoded embedding model name as a module constant. Acceptable as a default but not configurable without code change.

- **`conscio/session_rag.py:51`** — `OLLAMA_EMBED_MODEL = "nomic-embed-text"`. Same pattern.

---

## 2. Logic Bugs / Wiring Bugs

### CRITICAL — Metabolic context assessment uses wrong token count

- **`conscio/engine.py:469`** and **`conscio/agency/loop.py:131-133`** — Both call `MetabolicContext.assess(used_tokens, context_window)` where `used_tokens = self._state.total_tokens_approx()` / `state.total_tokens_approx()`. But `total_tokens_approx()` (`context_manager.py:137`) returns `len(self.to_injection()) // 4` — the size of the **consciousness state injection string** (~200–1000 tokens depending on mode), NOT the actual live session token usage. This means metabolic tiers (FATIGUE at 50%, CRITICAL at 70%) will essentially **never trigger** from this code path, because the injection is tiny relative to any context window (200 tokens / 200000 tokens = 0.1%). The `metabolic.py` docstring says `used_tokens: Tokens currently consumed in the live session (supplied by the caller — Conscio does not track it)` — but no caller supplies real session usage. The metabolic tier system is effectively dead code in the default wiring.

### BUG — `session_lifecycle.py:772` passes `"?"` as model name

- **`conscio/session_lifecycle.py:760,772`** — Line 760: `model=session.get("model", "?")` (default `"?"`). Line 772: `ConsciousnessEngine(model_name=summary.model or os.environ.get("CONSCIO_MODEL", ""))`. When `session.get("model")` returns `"?"`, `summary.model` is the string `"?"` which is **truthy**, so the `or` short-circuits and `"?"` is passed as the model name to `ConsciousnessEngine`. This produces a `ModelInfo` with a heuristic 128k context window and a misleading name. The env var fallback is never reached when the session has no model recorded.

### BUG — `cli.py:323` `_run_promote` ignores caller, may use empty model

- **`conscio/cli.py:323`** — `_run_promote` hardcodes `ConsciousnessEngine(model_name=DEFAULT_MODEL, ...)` where `DEFAULT_MODEL = os.environ.get("CONSCIO_MODEL", "")`. If `CONSCIO_MODEL` is unset, this passes `model_name=""`. Unlike `_cmd_info`/`_cmd_reflect` (which have a `model` positional arg), the `promote` subcommand has no `--model` flag, so there's no way to specify a model via CLI for promotion. Empty model name flows into `ModelRegistry.detect("")` → `lookup("")` returns None → heuristic `_extract_context_from_name("")` → 128k fallback. The engine builds but with a wrong/empty model identity. Should either add `--model` to `promote` or use `resolve_model_name()`.

### BUG — `dreaming.py:82-83` deprecated params still stored, never used

- **`conscio/dreaming.py:82-83,89-90`** — `DreamCycle.__init__` accepts `prune_min_relevance` and `prune_max_age_hours` (marked "deprecated, kept for back-compat"), stores them as `self.prune_min_relevance` / `self.prune_max_age_hours`, but the `run()` method never reads them — it uses `prune_by_entropy` with `prune_entropy_threshold` instead. These are dead fields. Not harmful but clutter.

---

## 3. Inconsistent Constants (same concept, different values)

### `frequent_errors()` `min_count` inconsistency

- **`conscio/meta_cognition.py:176`** — Default: `min_count=2`
- **`conscio/auto_evolution.py:226`** — Calls with `min_count=2`
- **`conscio/trust.py:48`** — Calls with `min_count=3` (`len(self.meta.frequent_errors(min_count=3))`)
- **`conscio/meta_cognition.py:237`** — `self.frequent_errors()` (uses default `min_count=2`)

Three call sites, two different thresholds for the same "frequent error" concept. The TrustMatrix is stricter (3+) than AutoEvolution/MetaCognition summary (2+). An error seen 2 times is "frequent" for evolution proposals but NOT for retry penalty calculation. Likely unintentional.

### Duplicate `MAX_BODY_BYTES` / `MAX_PAYLOAD_BYTES` (65536)

- **`conscio/hub/server.py:26`** — `MAX_BODY_BYTES = 65536`
- **`conscio/liaison/relay.py:14`** — `MAX_PAYLOAD_BYTES = 64 * 1024  # 65536 (R1)`

Same value, different names, no shared constant. If one needs to change, the other won't track.

### `DEFAULT_DB_PATH` duplicated in 3 files

- **`conscio/event_bus.py:59`** — `DEFAULT_DB_PATH = Path.home() / ".hermes" / "consciousness" / "conscio.db"`
- **`conscio/content_store.py:68`** — Same.
- **`conscio/token_tracker.py:26`** — Same.

Three independent copies of the same default path. Should be a shared constant.

### Hardcoded `100000` limit in `engine.py:931`

- **`conscio/engine.py:931`** — `reflect_count_fn=lambda: len(self.event_bus.query(type="reflection", limit=100000))`. Magic number `100000` for the reflection count query. Should be a named constant or use `limit=-1` semantics (but EventBus clamps negatives to 0 per `event_bus.py:254`). The intent is "all reflections" but `100000` is arbitrary.

---

## 4. Dead Code / Unreachable Paths

### `conscio/world_model.py:304-312` — `_compute_relevance` half-life mismatch

- **`conscio/world_model.py:309`** — Comment says `Lambda = 0.05 → half-life ~14 hours`, but `HALFLIFE_DAYS = 7` (line 20) is used for `entropy()` (line 337) while `_compute_relevance` uses a hardcoded `0.05` lambda. These are two different decay concepts (entropy age-normalization vs relevance decay), but the comment conflate them. The `0.05` magic number should be a named constant.

### `conscio/world_model.py:374-386` — `prune_irrelevant` appears unused

- `prune_irrelevant(min_relevance=0.1)` is defined but never called by `DreamCycle.run()` (which uses `prune_by_entropy`). May be dead code or a public API kept for external callers. No internal caller found.

### `conscio/agency/contracts.py:93` — `AuditVerdict.audited` default vs usage

- `audited: bool = True` is the default, but `ActPipeline._audit()` (`act.py:191-196`) constructs `AuditVerdict(verdict="PASS", audited=False)` for the fast-path. The default `True` is only hit when constructing from `verdict_from_dict` without the field. Minor inconsistency.

---

## 5. Missing Error Handling / Swallowed Exceptions

### Broad `except Exception: pass` — telemetry swallowing

- **`conscio/engine.py:182`** — `except Exception: pass` (telemetry emit in `__init__`). Comment says "telemetry must never crash init" — acceptable but logs nothing.
- **`conscio/engine.py:863,868,878,884`** — `close()` swallows all exceptions from `mod.close()` silently. A failing SQLite close could mask WAL corruption.
- **`conscio/agency/loop.py:193`** — `except Exception: pass` in `_emit_failure_brake`. Comment justifies it ("a strict bus must not crash run()"), but the failure-brake message itself is silently dropped if the bus is broken — the loop stops with no visible signal.

### `session_lifecycle.py` — 12 broad `except Exception as e` blocks

- **`conscio/session_lifecycle.py:499,510,515,520,525,535,543,548,554,560,839,845`** — Heavy use of `except Exception as e` with `logger.debug(...)`. The `record_session_lifecycle` path is designed to be non-fatal, but `debug` level means real failures are invisible in normal logging. At least one of these (`839`) wraps the entire enrichment+reflection block — a systemic failure degrades to a debug log.

### `conscio/semantic.py:66,80,94` — Three `except Exception` with no logging

- `semantic.py:66` — `_get_embedder` swallows import failure silently (sets `_embedder = None`).
- `semantic.py:80` — `available()` swallows embed failure (sets `_available = False`).
- `semantic.py:94` — `embed()` swallows per-text embed failure (returns `[]`).
All are intentional (offline-degradable design), but none log even at debug level — debugging "why is semantic mode off?" is hard.

---

## 6. Type / Default Inconsistencies

### `conscio/context_manager.py:69` — `context_window: int = 128_000` default

- `ConsciousnessState.context_window` defaults to `128_000` with a comment "Default MINIMAL threshold; overridden at init". But `ModelRegistry.MINIMAL_THRESHOLD` is also `128_000` (`models.py:147`). The default `ContextMode` is `COMPACT` (`context_manager.py:68`), which is inconsistent — `128_000` maps to `COMPACT` mode (>= 128k), not `MINIMAL` (< 128k). The default state claims COMPACT mode but a window at the MINIMAL threshold boundary. Confusing but overridden in practice.

### `conscio/agency/trust.py:92-98` — `autonomy_level` L3 check unreachable without `trips_since_fn`

- **`conscio/agency/trust.py:100-108`** — `_recent_trips()` returns `1` (sentinel) when `trips_since_fn is None`, making L3 unreachable. But the engine wires `trips_since_fn=self._trips_since` (`engine.py:932`), so this only affects standalone TrustMatrix construction. The fail-safe is documented but means L3 autonomy is **never reachable** in any construction path that doesn't explicitly wire the callback — a silent capability ceiling.

---

## 7. Stale Comments / Documentation Drift

### `conscio/goal_generator.py:90` — Stale source comment

- `self.source = source  # "internal" or "user"` — But `GoalOrigin` (`goal_generator.py:33-50`) defines 8 possible values: `user`, `internal`, `curiosity`, `anomaly`, `maintenance`, `meta_error`, `self_prompt`, `compaction`. The comment is from v1.0 and never updated.

### `conscio/models.py:27-29` — Mode threshold comments

- `MINIMAL = "minimal"     # < 128k tokens` — But `MINIMAL_THRESHOLD = 128_000` and `detect_mode` uses `< cls.MINIMAL_THRESHOLD`, so MINIMAL is `< 128000`, not `< 128k` (which would be 128000). Technically correct but the "k" abbreviation in the comment vs the exact `128_000` constant is slightly misleading.

---

## 8. Import / Export Inconsistencies

### `goal_fingerprint` imported from 3 different paths

- `conscio/agency/fingerprint.py` — definition (leaf module)
- `conscio/agency/act.py:29` — `from .fingerprint import goal_fingerprint` (re-exports)
- `conscio/agency/loop.py:20` — `from .act import ActReport, ActStatus, goal_fingerprint` (imports from act, not fingerprint)
- `conscio/agency/skills.py:119` — `from .act import goal_fingerprint` (runtime import from act)
- `conscio/agency/host_act.py:15` — `from .act import goal_fingerprint`
- `conscio/bench.py:29` — `from .agency.act import goal_fingerprint`
- `conscio/engine.py:1176` — `from .agency.act import goal_fingerprint` (runtime import)
- `conscio/noosphere/importer.py:12` — `from conscio.agency.fingerprint import goal_fingerprint` (imports from leaf)
- `conscio/noosphere/publish.py:17` — `from conscio.agency.fingerprint import goal_fingerprint` (imports from leaf)

Two import conventions exist: some import from `fingerprint` (the leaf), others from `act` (which re-exports). The `agency/__init__.py` does NOT export `goal_fingerprint`. This is fragile — if `act.py` stops re-exporting, 5 import sites break.

### `ToolSpec` and `registry_from_manifest` not exported from `agency/__init__.py`

- `conscio/agency/tools.py` defines `ToolSpec` (line 24) and `registry_from_manifest` (line 195), but `agency/__init__.py` only exports `Risk`, `ToolRegistry`, `make_default_registry`. `host_act.py` imports `registry_from_manifest` from `.tools` directly. External consumers must know the submodule path.

---

## 9. Config Key / Magic Number Issues

### `conscio/session_rag.py:41-43` — Hermes-specific hardcoded paths

- `HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))`
- `SESSION_DB = HERMES_HOME / "state.db"`
- `RAG_DB = HERMES_HOME / "consciousness" / "session_rag.db"`

These assume a Hermes Agent installation. Conscio as a standalone package has no `state.db`. The `SessionRAG` module will fail silently (empty results) when used outside Hermes. No fallback or clear error.

### `conscio/engine.py:197` — Hardcoded voice preset default

- `os.getenv("CONSCIO_VOICE_PRESET", "coherence-style")` — The default `"coherence-style"` is hardcoded. If `resolve_voice_preset` can't find it, behavior depends on that function's fallback (not audited here).

### `conscio/engine.py:207-209` — Output filter magic numbers

- `{"max_lines": {"max_lines": 200}}` and `{"truncate_lines": {"max_width": 8000}}` — Magic numbers `200` and `8000` inline in the pipeline construction. Should be named constants.

### `conscio/agency/skills.py:23-26` — Magic constants without config escape

- `MAX_PLAN_STEPS = 5`, `MIN_SERVE_RATE = 0.5`, `SIMILARITY_FLOOR = 0.2`, `MAX_EXEMPLARS = 2` — All hardcoded, none configurable. Tuning the skill library requires code changes.

### `conscio/agency/trust.py:17-23` — Trust thresholds hardcoded

- `PROBATION_EPOCH = 25`, `WARMUP_MIN_ROWS = 10`, `RETRY_CEILING = 4`, `AUTONOMY_WINDOW = 50`, `L2_ACCURACY = 0.7`, `L3_ACCURACY = 0.85`, `AUTONOMY_MIN_ROWS = 10` — All module-level constants, not configurable. The docstring says "Nothing hardcoded: every number is computed on the fly" but these are clearly hardcoded tuning knobs.

---

## 10. Minor Issues

### `conscio/agency/breaker.py:80` — `time.time()` vs `naive_utcnow`

- `now = time.time()` (epoch float) is used for quarantine timestamps, but `review_quarantine` (line 159) converts via `naive_utc_from_epoch(locked_at)`. Consistent within the module, but `time.time()` is used instead of the project's `timeutil` — a minor architectural inconsistency (the `test_no_bare_fromtimestamp_outside_timeutil` guard in `guards.py` docstring suggests this is a known concern).

### `conscio/world_model.py:311` — Magic `0.05` decay lambda

- `decay = math.exp(-0.05 * hours_since_update)` — Inline magic number. Should be `RELEVANCE_DECAY_LAMBDA = 0.05` or similar.

### `conscio/dreaming.py:23` — `MIN_ENTITY_MATCH_LEN = 3` unused outside `_friction`

- Defined at module level but only used in one method. Could be a class constant on `DreamCycle`.

---

## Summary of Highest Priority Fixes

1. **`adapters.py:233` / `providers.py:22`** — `claude-sonnet-4-6` doesn't exist in registry; fix to `claude-sonnet-4` or add registry entries.
2. **`engine.py:469` / `loop.py:131`** — Metabolic assessment uses injection size instead of real session usage; FATIGUE/CRITICAL never trigger.
3. **`session_lifecycle.py:772`** — `"?"` model name passed to engine when session has no model.
4. **`cli.py:323`** — `_run_promote` uses empty `DEFAULT_MODEL`; no `--model` flag on `promote` subcommand.
5. **`engine.py:118`** — Stale docstring example with hardcoded `"glm-5.1"` default.
6. **`trust.py:48` vs `auto_evolution.py:226`** — `frequent_errors(min_count=)` mismatch (3 vs 2).
