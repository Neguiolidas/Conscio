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
)
```

`ConsciousnessEngine` is a context manager (`with ConsciousnessEngine(...) as e:`).
Key methods: `reflect()`, `get_state_for_injection()`, `advisory()`, `recall()`,
`status()`, `attach_adapter()`, `probe()`, `act()`, `approve()`, `run()`,
`propose_action()`, `propose_plan()`, `load_structure()`, `structural_lookup()`,
`structural_signal()`, `structural_delta()`, `structural_freshness()`, `close()`.

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
`conscio.propose_action`, `conscio.propose_plan`. Resources: `conscio://advisory`,
`conscio://state`, `conscio://events`, `conscio://handoff`. See
[MCP server](../guides/mcp.md).

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
