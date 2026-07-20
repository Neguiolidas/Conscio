# Public API

The stable surface is what each package exports in its `__all__`. The top-level
`conscio` list below is complete; the per-subpackage lists show the key exports —
consult each package's `__all__` for the full set. Anything not exported is
internal and may change without notice.

## `conscio`

```python
from conscio import (
    ConsciousnessEngine,          # the orchestrator
    ContextManager, ContextMode,  # model-aware injection budget
    ModelRegistry,                # known model classes
    ContentStore, EventBus,       # substrate
    FilterPipeline, build_pipeline_from_dict,
    TokenTracker, Migrator,
    SessionSummary, record_session_lifecycle,
    MetabolicContext, MetabolicState,
    DreamCycle, DreamReport,
    # Inference backends
    MockAdapter, OllamaAdapter, LlamaCppAdapter, OpenAICompatAdapter,
    # Shared safety vocabulary + perception surface
    Risk, SensorAdapter, PerceptionFrame, MockSensor,
    # Self-evaluation (v2.15)
    evaluate, EvaluationReport, AxisScore,
    # Gate tools (v3.0 — Ato 1)
    decide, council, loop_gate, delivery_check, investigate,
    ADR_VALID_STATUSES, COUNCIL_ROLES, COUNCIL_VOTES,
    # Pipeline tools (v3.0 — Ato 2)
    acceptance_criteria, verify, continuous_loop, strategic_compact, ledger,
    LOOP_PATTERNS, PROMOTION_GATES,
    # Diagnostic tools (v3.0 — Ato 3)
    context_budget, eval_harness, rules_distill,
    EVAL_CAPABILITY, EVAL_REGRESSION, EVAL_BENCHMARK,
)
```

`ConsciousnessEngine` is a context manager (`with ConsciousnessEngine(...) as e:`).
Key methods: `reflect()`, `get_state_for_injection()`, `advisory()`, `recall()`,
`status()`, `attach_adapter()`, `probe()`, `act()`, `approve()`, `run()`,
`propose_action()`, `propose_plan()`, `load_structure()`, `structural_lookup()`,
`structural_signal()`, `structural_delta()`, `structural_freshness()`, `close()`.

**v2.15 — Self-evaluation:** `evaluate(task_description, output)` → 5-axis rubric
(accuracy, completeness, clarity, actionability, conciseness), scores 1–5 each.

**v3.0 — Gate tools (Ato 1):** `decide(title, context, status, …)`,
`council(question, …)`, `loop_gate(verifiable, budget_ok, has_tools)`,
`delivery_check()`, `investigate(target, action_type)`. Each emits a typed EventBus
event and returns a dict. `delivery_check()` also runs automatically in `close()`.

**v3.0 — Pipeline tools (Ato 2):** `acceptance_criteria(goal, depth, …)`,
`verify(criteria, criteria_source)`, `continuous_loop(task, pattern, …)`,
`strategic_compact(phase, context_tokens, context_window)`,
`ledger(action, rollout_id, …)`. Advisory, deterministic, EventBus-backed.

**v3.0 — Diagnostic tools (Ato 3):** `context_budget(context_tokens, …)`,
`eval_harness(action, eval_id, …)`, `rules_distill(action, …)`. Read-only audits
and pattern extraction.

`advisory()` is the structured, read-only pull surface a host consumes each turn
(goals tagged by provenance, lockdown/brake status). See
[Consuming awake output](../guides/integration.md).

`load_structure(path)` ingests a Graphify `graph.json` (data, never code; R10)
and distils it; `get_state_for_injection()` then appends a budget-adaptive
structure block, and `structural_lookup(id)` / `structural_signal()` are the
read-only drill-down surfaces. `conscio.structural` exports `StructuralDistiller`,
`StructuralSignal`, `Hyperedge`, `CommunitySummary`, `GraphNode`,
`StructuralError`. See [Structural cognition](../guides/integration.md#structural-cognition).

`load_structure(path, workspace_id=…, root=…)` additionally tracks **drift**
(vs the persisted per-workspace baseline) and **freshness** (graph commit vs the
repo `HEAD`, read purely from `.git`); `structural_delta()` /
`structural_freshness()` expose them and they appear in
`advisory()["structural"]`. `conscio.structural_drift` exports `StructuralDigest`,
`StructuralDelta`, `StructuralFreshness`, `StructuralDriftStore`, `compute_delta`,
`compute_freshness`, `read_head_commit`, `drift_path`. See
[Drift & freshness](../guides/integration.md#drift-freshness-v18).

## `conscio.agency`

The audited action layer (key exports; see `conscio.agency.__all__` for the full
set, including the GBNF grammar compilers and tier-selection helpers):

```python
from conscio.agency import (
    ActPipeline, ActReport, ActStatus,
    InferenceAdapter, InferenceResult, AdapterCaps, AdapterError,
    Meter, MeteredAdapter, MockAdapter,
    OllamaAdapter, LlamaCppAdapter, OpenAICompatAdapter,
    OutputGateway, GatewayError,
    ActionProposal, AuditVerdict, ToolResult, validate,
    ActionLedger, CircuitBreaker, DEFAULT_MAX_RETRIES,
    Skeptic, TrustMatrix, SkillLibrary,
    Risk, ToolRegistry, make_default_registry,
    ProbeSuite, ModelProfile, GoalArbiter, AutonomyLoop, RunReport, ActBudget,
)
```

`propose_action(intent)` and `propose_plan(goal, tools)` are the **propose-only**
cognition surfaces added in v2.0: they compose the existing Actor/Skeptic, return
an audited `{verdict, reasons, risk_flags, confidence, proposal}`, **never
execute**, and fail closed (`verdict: FAIL`) without an attached adapter. They are
what the MCP server exposes to a host; a direct caller can use them too.

## `conscio.mcp`

The embodiment surface (v2.0) — embed Conscio in any MCP host. Normally driven via
the `conscio-mcp` console script, not imported:

```python
from conscio.mcp import serve, main      # stdio serve loop + CLI entry point
```

`conscio-mcp` speaks newline-delimited JSON-RPC 2.0 over stdio (stdlib only,
nothing opens a socket). Propose-only tools: `conscio.feed`/`conscio.note`
(idempotent on `event.id`), `conscio.advisory`, `conscio.recall`,
`conscio.propose_action`, `conscio.propose_plan`.

**v2.15:** `conscio.evaluate` (5-axis self-evaluation scorecard).

**v3.0 — Gate tools:** `conscio.decide`, `conscio.council`, `conscio.loop_gate`,
`conscio.delivery_check`, `conscio.investigate`.

**v3.0 — Pipeline tools:** `conscio.acceptance_criteria`, `conscio.verify`,
`conscio.continuous_loop`, `conscio.strategic_compact`, `conscio.ledger`.

**v3.0 — Diagnostic tools:** `conscio.context_budget`, `conscio.eval_harness`,
`conscio.rules_distill`.

Resources: `conscio://advisory`, `conscio://state`, `conscio://events`,
`conscio://handoff`. See [MCP server](../guides/mcp.md).

## `conscio.perception`

The pluggable perception surface:

```python
from conscio.perception import SensorAdapter, PerceptionFrame, MockSensor
```

- `PerceptionFrame(source, observations, signals={}, ts=0.0)` —
  `.to_world_state()` builds the deterministic string `reflect()` accepts.
- `SensorAdapter` — abstract base; implement `perceive() -> PerceptionFrame`.
- `MockSensor(frames)` — deterministic test double.

## `conscio.plugins`

Entry-point discovery (see [Plugins](../guides/plugins.md)):

```python
from conscio.plugins import (
    load_entry_points, discover_adapters, discover_sensors, discover_tools,
)
```

## `conscio.risk`

```python
from conscio.risk import Risk    # LOW | MEDIUM | HIGH
```

The single safety-tier vocabulary shared by the action and perception surfaces.
`conscio.agency.tools.Risk` re-exports the same object for backward compatibility.
