# Conscio — Claims Ledger

A framework about self-knowledge should know what it can and cannot prove
about itself. Every load-bearing claim Conscio makes, mapped to evidence.

**Status:** PROVEN (test) · MEASURED (real backend) · PARTIAL · UNPROVEN.
Updated each phase. Current as of **v1.8.0** (2026-06-18).

| # | Claim | Evidence | Status |
|---|-------|----------|--------|
| 1 | `reflect()` is passive — never calls an LLM | `reflect()` has no adapter dependency; all inference lives behind `act()` (`conscio/engine.py`) | PROVEN |
| 2 | Core is zero-deps (numpy + sqlite3 only) | `pyproject.toml` dependencies = `["numpy>=1.24"]`; sqlite3 is stdlib | PROVEN |
| 3 | Deterministic sabotage is 100% blocked, zero executions | `tests/test_agency_adversarial.py::test_a3_deterministic_sabotage_100_percent_blocked`, `::test_a3_nothing_ever_executes` | PROVEN |
| 4 | The Skeptic catches semantic sabotage (machinery) | bench semantic catch-rate = 1.0 with MockAdapter (1.0-by-construction) | PARTIAL (mock) |
| 5 | The Skeptic catches semantic sabotage **on a real ≤4B model** | `docs/bench/v1.2-immune-qwen3.5-0.8b.json`: semantic catch-rate **1.0** (qwen3.5-0.8b, open mode) | MEASURED |
| 6 | A small model **inherits competence from its own history** | `docs/bench/v1.2-curve-qwen3.5-0.8b.json`: `exec_ok` rose **0.2 → 1.0** once Distill served past successes as few-shot | MEASURED |
| 7 | Autonomy is earned, not hardcoded (L3 needs ≥0.75 cal, ≥0.85 acc, zero recent trips) | `tests/test_agency_trust.py`, `tests/test_agency_arbiter.py` | PROVEN (logic) |
| 8 | Global lockdown persists across an engine restart | `tests/test_agency_engine_immunity.py::test_engine_breaker_lockdown_persists_across_restart` | PROVEN |
| 9 | Tool errors never leak tracebacks / absolute paths | `tests/test_agency_tools.py::test_tool_error_has_no_traceback` | PROVEN |
| 10 | `approve()` cannot double-execute (atomic claim) | `tests/test_agency_act.py::test_double_approve_executes_once`, `tests/test_agency_ledger.py::test_claim_is_won_exactly_once` | PROVEN |
| 11 | Decode validity / skeptic mode are measured per-model, not assumed | `docs/bench/v1.2-immune-*.json` `profile` block (probed: json_fidelity, schema_depth, kv_ok) | MEASURED |
| 12 | The bench survives a backend that is down or dies mid-run | `tests/test_agency_bench.py::TestBackendDown`, `::TestSkillCurveCrashSafe` | PROVEN |
| 13 | Perception plugs in **without touching `reflect()`** | `tests/test_perception.py::test_frame_roundtrips_into_reflect`; `conscio/engine.py` unchanged this phase | PROVEN |
| 14 | The plugin surface is discoverable and **resilient to a bad plugin** | `tests/test_plugins.py` (valid/wrong-type/load-failure/one-bad-doesn't-hide-good) | PROVEN |
| 15 | The package ships types (PEP 561) | `conscio/py.typed` present in the built wheel; `mypy conscio/` is a real gate | PROVEN |
| 16 | The docs site builds clean with no internal leak | `mkdocs build --strict` green; `exclude_docs` + a `site/` grep verify no internal/forward-looking page renders | PROVEN |
| 17 | Installable as a wheel; core pulls only numpy | wheel + sdist pass `twine check`; fresh-venv install resolves `conscio` + `numpy` only; **live on PyPI since v1.3.0** (`pip install conscio`) | PROVEN |
| 18 | Engine/context construction is offline & deterministic by default | `tests/test_model_offline_default.py` (known-model `detect()` does zero fs/net I/O via tripwires; `tests/test_metabolic.py` passes with no config isolation) | PROVEN |
| 19 | Host-state model-context auto-detection is opt-in (never default) | `tests/test_model_offline_default.py`, `tests/test_model_auto_detect.py::TestJsonConfig` (config consulted only under `autodetect`/`CONSCIO_AUTODETECT`) | PROVEN |
| 20 | Config path has no optional-dependency footgun | config is stdlib JSON; `tests/test_model_auto_detect.py::TestJsonConfig::test_no_yaml_dependency_in_import_graph` | PROVEN |
| 21 | The vector store cannot be corrupted by a changed embedding model | `tests/test_embedder.py::TestStoreDimSafety` (wrong-dim dropped on write, skipped on search, re-index on `(model,dim)` change) | PROVEN |
| 22 | Runs on frontier APIs (Claude, Gemini) as well as local backends | `tests/test_agency_adapters_http.py::TestAnthropic`/`::TestGemini` (request shape + response parsing + auth headers + key handling, against a loopback fake) | PROVEN (wire format; live API is environmental) |
| 23 | Reaches GPT + any OpenAI-compatible cloud endpoint (not just localhost) | `tests/test_agency_adapters_http.py::TestOpenAICloudEndpoint` (custom cloud `base_url` + Bearer key) / `::TestOpenAI` (`OpenAIAdapter` cloud default + `OPENAI_API_KEY`) | PROVEN (wire format) |
| 24 | Autonomous operation is gated by Awake Mode (R9); default OFF | `tests/test_awake.py` (default asleep; asleep `run()` = reflect-only with zero act/dream; awake runs the loop; direct `act()` not gated) | PROVEN |
| 25 | The awake flag persists (survives reflect rebuild, lockdown, and restart) | `tests/test_awake.py::test_wake_persists_across_reopen`/`::test_awake_survives_reflect_cycle`/`::test_act_lockdown_does_not_clobber_persisted_awake`; old states load asleep | PROVEN |
| 26 | The daemon isolates a failing sensor and survives restart | `tests/test_daemon.py::test_failing_sensor_is_isolated`, `::test_run_once_then_shutdown_writes_heartbeat_and_releases_pid`; state resumes from existing persistence | PROVEN |
| 27 | Reference sensors are read-only (`Risk.LOW`) and never raise | `tests/test_host_sensor.py` (every probe guarded, non-Linux/bad-port safe), `tests/test_agent_sensor.py::test_read_only_does_not_mutate_peer` (byte-identical peer) | PROVEN |
| 28 | Workspace root + env class are detected and changes are signalled | `tests/test_workspace.py` (explicit/env/git/cwd resolution, EnvClass, `workspace:changed` on root change) | PROVEN |
| 29 | A single daemon holds a state dir (advisory pidfile, stale-pid reclaim) | `tests/test_daemon.py::test_pidfile_blocks_second_daemon`/`::test_stale_pidfile_is_reclaimed` | PROVEN |
| 30 | Diagnostic goals (meta_error/self_prompt/compaction) never auto-run; stay visible | `tests/test_goal_provenance.py` (origin taxonomy, arbiter skips diagnostic, advisory still shows them) | PROVEN |
| 31 | `advisory()` is a cheap read-only pull — no LLM, no state mutation | `tests/test_engine_advisory.py` (no adapter required; goals tagged by provenance; lockdown/brake status) | PROVEN |
| 32 | An imported code graph is consumed as **data, never code** (R10) | `tests/test_structural.py` (json-only parse; a code-looking label is returned verbatim, never evaluated; no `networkx`/`eval`/`exec`/`pickle` import) | PROVEN |
| 33 | Structural injection is additive — the consciousness-state block is byte-identical | `tests/test_structural_inject.py` (`get_state_for_injection()` unchanged with no graph; appends labels-only, never raw node-ids) | PROVEN |
| 34 | Structural ingestion is consent-gated (default OFF) and switch-safe | `tests/test_structural_consent.py`, `tests/test_daemon_structure_sync.py` (unconsented switch unloads — no cross-project leak) | PROVEN |
| 35 | The agent detects structural **drift** vs a persisted per-workspace baseline | `tests/test_structural_drift.py`, `tests/test_structural_inject.py::TestStructuralDrift` (commit/hash/community/hyperedge diff by id; `structure:changed` emitted) | PROVEN |
| 36 | **Freshness** vs the repo HEAD is read **purely from `.git`** — no `git` subprocess | `tests/test_structural_drift.py` (ref/packed-refs/detached/worktree `.git`-file; `test_module_uses_no_subprocess_or_shell`) | PROVEN |
| 37 | Drift never raises into the host loop (corrupt store / unreadable `.git`) | `tests/test_structural_drift.py` (corrupt/non-dict store → empty; save-failure swallowed; malformed `.git` → None) | PROVEN |
| 38 | Direct `act()` is intentionally **not** awake-gated (human escape hatch) — still fully governed by the ActPipeline; autonomy (the daemon) is gated via `run()` | `tests/test_awake.py::test_run_asleep_reflects_but_does_not_act`, `::test_direct_act_works_while_asleep`; `tests/test_daemon.py::test_try_break_asleep_daemon_runs_a_cycle_but_never_acts` | PROVEN |
| 39 | The engine survives a **corrupt store at construction** — quarantines + recreates, never crashes the host (I-S4) | `tests/test_engine_init.py` (garbage / truncated `conscio.db` → constructs, `advisory()` works, corrupt file preserved as `.corrupt-<ts>`) | PROVEN |

## Honest limits (what is NOT proven)

- **Calibration is weak on small models.** The v1.2 immune run measured
  calibration 0.50 — the 0.8B model reaches the right verdict but isn't
  confident. Not a solved problem.
- **n = 1 model.** Claims 5, 6, 11 rest on a single 0.8B model on one CPU.
  Directional, not a population result. Widening the model sweep is future
  work (the campaign protocol is reusable).
- **Reasoning-distilled small models are not yet supported** for strict
  structured decode (verbose chain-of-thought breaks extraction). See
  `docs/bench/v1.2-skill-curve.md`.
- **Adapters are not an "inside-tool" surface.** The inference adapters
  (`AnthropicAdapter`/`GeminiAdapter`/`OpenAIAdapter`/…) are LLM-API callers for
  Conscio's *own* cognition — they do **not** make Conscio run *inside* Claude Code
  or Antigravity. A turnkey plug-in / MCP server / IDE extension is v2.0 "Connect"
  work, not shipped today. Host integration today is the documented contract
  (`engine.advisory()` + `daemon_heartbeat.json` + `SensorAdapter`).
- **Corrupt-store recovery discards live history.** When `conscio.db` is corrupt
  at startup it is quarantined to `conscio.db.corrupt-<ts>` (preserved on disk,
  never auto-replayed) and a fresh DB is created — so the engine's prior cognitive
  history is not in the live store after recovery. A `storage_recovered` event +
  WARNING log record it; pruning the accumulated quarantine files is v2.0 work.
- Claims about not-yet-shipped capabilities do **not** appear here. This ledger
  records only what exists today.
