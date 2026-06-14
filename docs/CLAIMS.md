# Conscio — Claims Ledger

A framework about self-knowledge should know what it can and cannot prove
about itself. Every load-bearing claim Conscio makes, mapped to evidence.

**Status:** PROVEN (test) · MEASURED (real backend) · PARTIAL · UNPROVEN.
Updated each phase. Current as of **v1.2.0** (2026-06-14).

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
- Claims about the daemon, the society, and Awake Mode do **not** appear here
  because those features have not shipped. This ledger only records what
  exists.
