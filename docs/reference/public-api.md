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
Key methods: `reflect()`, `get_state_for_injection()`, `recall()`, `status()`,
`attach_adapter()`, `probe()`, `act()`, `approve()`, `run()`, `close()`.

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
