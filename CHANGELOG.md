# Changelog — Conscio

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.5.1] — 2026-06-17

"Awake Hardening" — fixes from the first real-world Awake Mode run (in the
Hermes-Agent environment). No new cognition; this closes correctness/safety
holes the green test suite missed and that only a live deployment surfaced.

### Fixed

- **Meta-goal feedback loop → lockdown (#6).** Recurring act errors (e.g.
  `act:tool:skeptic_fail`) were turned into an actor-executable
  `Maintenance: fix_recurring_error` goal, which the model then ran *literally*
  (`fs_read path="skeptic_fail"`) → more errors → more goals → lockdown spiral.
  Error patterns no longer mint executable goals; they remain visible through
  the existing diagnostic channels (meta-cognition, reflection, and
  `AutoEvolution.observe_errors()` — a reviewed `PATTERN_LEARN` proposal queue
  that never reaches the act pipeline). The general goal-provenance gate lands
  in v1.6.
- **Single `_RAG_DISABLED` sentinel.** The engine and content-layer each defined
  their own `object()` sentinel; tests that imported the engine's and assigned it
  to the content-layer leaked a bare object into `recall()` (swallowed by a broad
  `except`), so RAG was never actually disabled — green but wrong. The sentinel is
  now owned by `content_layer` and re-exported from `engine` (one object).
- **CLI storage no longer ephemeral.** The default storage dir was a fresh
  `mkdtemp()` per invocation, so `conscio awake` then `conscio sleep` never shared
  state. It now persists under `HERMES_HOME` (default `~/.hermes/consciousness`,
  env-overridable), consistent with `session_lifecycle`/`session_rag`.
- **CircuitBreaker quarantine-release crash.** The release path emitted an
  event of type `"status"`, which is not in the event-bus whitelist and raised
  `ValueError`. Emits a valid `"system"` event now.

### Added

- **Aggregate failure-rate brake (#8).** `ActBudget` gains `max_failure_rate`
  (default `0.5`) and `min_attempts` (default `4`); the autonomy loop stops the
  heartbeat (`stopped="failure_rate"`, plus a surfaced event) once the share of
  failed cycles crosses the threshold. This complements the per-goal
  `CircuitBreaker` (which only catches a *single* goal's consecutive failures)
  by catching broad flailing across many distinct goals/tools — the field
  failure mode. Set `max_failure_rate >= 1.0` to disable.
- **Daemon adapter wiring.** The daemon can now build its inference adapter from
  `~/.config/conscio/config.json` (or `--adapter/--base-url/--adapter-model`),
  closing the v1.5 gap where an awake daemon had no backend; the heartbeat is
  written every cycle so peers can read it.

---

## [1.5.0] — 2026-06-15

"Live" — Conscio stops being a one-shot advisory call and becomes a **living
process**: it runs continuously as a daemon, **perceives** the world through
pluggable sensors, and acts autonomously only when **explicitly awake** — a
gated state, off by default, auditable as a single switch. No new cognition:
`reflect()`, `act()`, dream, and metabolism are reused verbatim; this is the
wiring that lets the existing loop run on live input under a hard safety gate.

### Added

- **Awake Mode (R9) — the master gate for autonomous operation.** A persisted
  `awake` boolean on `ConsciousnessState` (**default OFF**), surfaced as
  `engine.awake` / `engine.wake()` / `engine.sleep()` and `conscio awake|sleep`.
  The self-initiated heartbeat (`engine.run()` and the daemon) is gated by it:
  **asleep ⇒ perceive + `reflect()` only, zero arbiter/act/dream**; awake ⇒ the
  full existing loop. A direct human `engine.act()` call is *not* gated (R9
  governs self-initiated autonomy only). Toggling emits an auditable
  `awake:changed` event; the flag survives `reflect()` rebuilds and restart.
  **R9 is the first new safety rule since R8.**
- **Reference sensors** (concrete implementations of the frozen v1.3
  `SensorAdapter`): `HostSensor` — read-only host facts (load/disk/memory/top
  processes + opt-in loopback service liveness), every probe guarded, non-Linux
  safe, `Risk.LOW`; `AgentSensor` — read another Conscio-backed agent's session
  state (open goals, last reflection, handoff) strictly read-only, `Risk.LOW`.
  Both registered as `conscio.sensors` entry points (`conscio plugins` lists
  them).
- **Daemon** (`conscio/daemon.py` + `conscio-daemon` console script). A
  persistent heartbeat: per cycle it polls a sensor **list** (each guarded — a
  failing sensor never kills the loop), assembles `world_state`, calls
  `engine.run()` (awake- and metabolic-gated), fires an `on_cycle` hook, and
  polls the workspace. Graceful `SIGTERM`/`SIGINT`, advisory pidfile
  (single-daemon-per-state-dir, stale-pid reclaim), heartbeat on shutdown, and
  resume-from-persisted-state on boot. `conscio daemon …` delegates to it.
- **`WorkspaceContext`** (`conscio/workspace.py`) — environment & workspace
  awareness: resolves the active workspace root (explicit → `CONSCIO_WORKSPACE`
  → git-root → cwd), a stable `id`, and an `EnvClass` (`STABLE` for IDE/CLI,
  `SWITCHING` for workspace-hopping agents like OpenClaw/Hermes, `UNKNOWN`
  treated as `SWITCHING`); emits `workspace:changed` when the root changes. The
  seam the v1.6 structural layer keys its per-workspace scope/consent off.
- **`OpenAIAdapter`** — GPT over the official OpenAI cloud endpoint with
  `OPENAI_API_KEY` from the env, mirroring the Claude/Gemini convenience. The
  generic `OpenAICompatAdapter` already reaches **any** OpenAI-compatible cloud
  provider (Groq, Together, OpenRouter, DeepSeek, Fireworks, cloud vLLM…) via
  `base_url` + Bearer `api_key`; `OpenAIAdapter` only pins the OpenAI default so
  the env-key read is safe (the generic adapter defaults to localhost).

### Safety

- **R9 (Awake gate)** added to the non-negotiable safety rules; asleep =
  advisory reflect-only, awake = full autonomy, default OFF. R1–R8 intact; R7
  (no network in the ToolRegistry) unaffected — the daemon perceives locally and
  acts only through the already-audited pipeline.

### Notes

- Zero new runtime dependencies (stdlib + numpy + sqlite3 core preserved).
  `reflect()` semantics unchanged. No filesystem-watcher — polling only (robust
  in containers/CI). Skeptical-review hardening folded in: `awake` survives an
  `act()` lockdown that persists a transient state; the host port probe never
  raises on a bad port; an awake heartbeat with no inference backend still
  perceives+reflects (observation is always-on).

---

## [1.4.0] — 2026-06-15

"Attune" — Conscio perceives the context window of whatever model/backend it runs
on, and the session-RAG embedder works against any OpenAI-compatible endpoint —
without making construction touch the filesystem/network by default, lose
determinism, add a dependency, or corrupt the vector store.

### Added

- **Frontier inference adapters.** `AnthropicAdapter` (Claude Messages API) and
  `GeminiAdapter` (Gemini `generateContent`) join the local adapters (Ollama,
  llama.cpp, OpenAI-compatible, LM Studio) — stdlib `urllib` only, keys from
  `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`|`GEMINI_API_KEY`. Conscio now runs on the
  backends behind **Claude Code** and **Antigravity**, removing the lock to local
  models. Inference is infrastructure, not a tool an actor can invoke, so **R7 (no
  network in the ToolRegistry) is unaffected** — this only widens the
  `InferenceAdapter` carve-out from localhost to a configured frontier API
  (operator's own key, operator's choice). Gemini context windows added to the
  registry (`gemini-2.5-pro`/`-flash`, `gemini-1.5-pro`, alias `gemini`).
- **Opt-in host-state auto-detection.** `ModelRegistry.detect(...)` and
  `ContextManager(...)` gained `autodetect` (default `False`) and `base_url`
  (default `None`). With `autodetect=True` (or env `CONSCIO_AUTODETECT`) Conscio
  consults a config file, LM Studio's active state, and GGUF metadata; an explicit
  `base_url` enables a targeted OpenAI-compatible endpoint probe.
- **JSON config** at `~/.config/conscio/config.json` (and `~/.conscio/config.json`),
  nested (`{"models": {name: {"context_window": N}}}`) or flat
  (`{"context_window": {name: N}}`).
- **Embedder identity in the vector store** — the store persists the
  `(embedding model, dim)` it was built with and triggers a clean re-index when
  the configured embedder changes.

### Changed

- **Model-context detection is offline & deterministic by default.** A *known*
  model with no explicit override resolves to the curated registry with **zero**
  filesystem/network I/O — restoring the behavior the auto-detection commits had
  broken (engine/context construction no longer scans `$HOME`).
- **Config is stdlib JSON, not YAML** — the detection path no longer depends on an
  optional PyYAML package that could silently disable the feature.
- GGUF-derived context is labelled as the **architectural max** (may exceed the
  active/loaded context); active sources (endpoint, LM Studio) are preferred.

### Fixed

- **GGUF parser** no longer aborts on array-valued metadata — it previously
  returned `None` at the first array key (e.g. tokenizer tokens) before reaching
  `context_length`.
- **Session-RAG search** can no longer crash on a dimension-mismatched vector
  (`np.dot` shape error): wrong-dim vectors are dropped on write and skipped on
  search, preventing store corruption when the embedding model changes.
- Removed a test monkeypatch that had masked the offline/determinism regression.

---

## [1.3.1] — 2026-06-14

### Changed

- **CLI** — the `info`/`reflect` model now flows from a named `DEFAULT_MODEL`
  constant, and an unrecognized model name prints a clear note (to stderr)
  explaining the heuristic context window used and how to register the model,
  instead of silently falling back.
- **`PerceptionFrame.ts`** documented as **epoch seconds** (`time.time()`,
  matching the `ActionLedger` `ts REAL` convention; `0.0` = unset) and explicitly
  excluded from `to_world_state()` so determinism holds.

### Tests

- CLI: added a subprocess end-to-end test of `python -m conscio` (covers
  `__main__.py`) and a test for the unknown-model note; version assertion is now
  bump-proof.
- `Risk`: added JSON serialization / wire-value-stability and round-trip tests.

---

## [1.3.0] — 2026-06-14

### Added

- **PyPI packaging** — `pip install conscio`. Single-source version (read
  dynamically from `conscio.__version__`), console scripts (`conscio`,
  `conscio-bench`), PEP 561 `py.typed` marker, `MANIFEST.in`, and 3.13 +
  `Typing :: Typed` classifiers. Wheel + sdist build clean and pass
  `twine check`; the published artifact pulls only `numpy`.
- **`conscio` CLI** — `version` / `info` / `reflect` / `plugins` / `bench`
  (the last delegates verbatim to `conscio.bench`). Offline-safe;
  `python -m conscio` works too.
- **Public plugin surface** — three stable, documented extension points
  discoverable via `importlib.metadata` entry points:
  - `conscio.adapters` — custom `InferenceAdapter` backends.
  - `conscio.sensors` — the new **`SensorAdapter`** perception interface
    (`conscio.perception`: `SensorAdapter`, `PerceptionFrame`, `MockSensor`);
    a sensor's `PerceptionFrame.to_world_state()` feeds `reflect()`, which is
    untouched.
  - `conscio.tools` — custom tool factories.
  `conscio.plugins` discovers them resiliently — a broken or mistyped
  third-party plugin is skipped with a warning, never crashing the host.
- **Docs site** — MkDocs Material (dev-only extra): guides, a curated public-API
  reference, the claims ledger, and the bench reports. `mkdocs build --strict`
  is green; the core stays zero-dependency.
- **Release automation** — `release.yml` (tag `v*` → gated build → PyPI via OIDC
  trusted publishing), `docs.yml` (push to `main` → GitHub Pages), and a CI
  build-smoke job. `docs/RELEASING.md` runbook.
- **Examples gallery** — `custom_adapter.py`, `host_guardian.py`,
  `agent_companion.py`: runnable, offline, each exercising one extension point
  (smoke-tested in CI).

### Changed

- **`Risk` enum** moved to `conscio.risk` as the single safety-tier vocabulary
  shared by the action and perception surfaces. `conscio.agency.tools` re-exports
  it — every historical import path resolves to the same object (no behavior
  change).

---

## [1.2.0] — 2026-06-14

### Added

- **F2-deferred hardening closed** (debt-zero before the organism):
  - `validate` rejects empty/whitespace required strings (`non_empty` rule on
    `PROPOSAL_SCHEMA.tool`).
  - `fs_read` enforces `MAX_READ_BYTES` (1 MB), matching `fs_write`.
  - Tool errors carry `Type: message` only — no traceback, no path leak.
  - `HTTPError` maps to `AdapterBadResponse` (a 4xx/5xx is a bad response,
    not a connection failure).
  - `ActionLedger` sets `busy_timeout=5000` for concurrent writers.
  - `approve()` claims the row atomically (`proposed → executing`) as the sole
    gate — a concurrent or repeated approve can never double-execute.
  - e2e test: global breaker lockdown persists across an engine restart.
- **Bench real-backend hardening:**
  - Clean non-zero exit when the backend is unreachable (no traceback, no
    report of misleading zeros).
  - Crash-safe incremental skill-curve output (atomic write after every
    bucket) tagged `complete` | `aborted`; backend death detected via the new
    `OutputGateway.last_adapter_error` signal (without changing the act
    path's `GatewayError` flow).
- **LM Studio backend** — `LMStudioAdapter` (OpenAI-compatible, default
  `:1234`) and the `lmstudio:<model>` bench spec. LM Studio rejects
  `response_format=json_object`, so the adapter omits it and lets the gateway
  drive JSON decoding (an `OpenAICompatAdapter._response_format()` hook).
- **Measured proof (v1.2 "Prove"):**
  - `docs/bench/v1.2-campaign.md` — reproducible campaign protocol.
  - `docs/bench/v1.2-skill-curve.md` + JSON artifacts — first real-backend
    measurement: on `qwen3.5-0.8b` (LM Studio, CPU) execution success rose
    0.2 → 1.0 once Distill served past successes as few-shot, and the
    Skeptic's semantic catch-rate was 1.0.
  - `docs/CLAIMS.md` — honesty ledger mapping every claim to its evidence.

### Notes

- `reflect()` untouched; zero-deps core (numpy + sqlite3) intact.
- +21 tests (**984 total**); mypy a real gate; ruff clean; per-file test loop.

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
