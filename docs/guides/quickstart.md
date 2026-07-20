# Quickstart

## Passive consciousness — `reflect()`

`reflect()` is safe by construction: no LLM calls, no side effects, append-only.

```python
from conscio import ConsciousnessEngine

# Auto-detects model class and context mode.
with ConsciousnessEngine(model_name="kimi-k2.6") as engine:
    result = engine.reflect(
        world_state="All systems operational",
        confidence=0.8,
        anomalies=["Unusual latency spike detected"],
    )

    # Compact state for prompt injection (bounded by the context mode).
    injection = engine.get_state_for_injection()

    # Query / update the world model.
    engine.world.add_entity("server", "system", state="healthy")
    engine.world.query("server health")

    # Cross-session memory (ContentStore FTS5 + optional SessionRAG).
    hits = engine.recall("latency incidents")
```

## Opt-in agency — `act()` (propose-only by default)

```python
from conscio.agency import OllamaAdapter

engine.attach_adapter(OllamaAdapter(model="qwen3.5:0.8b"))

report = engine.act()                 # downstream of reflect(); proposes only (L1)
if report.status.value == "proposed":
    print(report.proposal.tool, report.proposal.args)
    engine.approve(report.ledger_id)  # the human gate executes it
```

Autonomy is **earned and measured**, never assumed: `ProbeSuite` measures the
attached model, `TrustMatrix` grants L1/L2/L3 from real calibration and ledger
history, and the `CircuitBreaker` quarantines misbehaving goals. HIGH-risk
actions are *always* queued for a human (rule R6).

```python
engine.probe()            # lazy, empirical capability measurement
engine.run(budget=...)    # L3 heartbeat: reflect → act → dream, gated
```

## Feeding perception in

A `SensorAdapter` turns the world into a `PerceptionFrame`; its
`to_world_state()` is the string `reflect()` already accepts — so perception
plugs in without touching `reflect()`:

```python
from conscio.perception import PerceptionFrame

frame = PerceptionFrame(source="host", observations=["disk 40% used"])
engine.reflect(world_state=frame.to_world_state())
```

See [Plugins](plugins.md) for writing your own sensors, adapters, and tools.

## ECC tools — G-P-D (v3.0)

13 advisory tools across three modules, all deterministic and EventBus-backed:

```python
# Gates — decision architecture
adr = engine.decide(title="Use SQLite", context="Need local storage", status="proposed")
result = engine.council(question="Should we use SQLite or PostgreSQL?")
ok = engine.loop_gate(verifiable=True, budget_ok=True, has_tools=True)
check = engine.delivery_check()      # also runs automatically in engine.close()
engine.investigate(target="config.py")  # verify you read it before acting

# Pipelines — workflow management
criteria = engine.acceptance_criteria(goal="Add API endpoint", depth="full")
result = engine.verify(criteria_source="acceptance")
loop = engine.continuous_loop(task="Run CI on every PR", frequency="daily")
compact = engine.strategic_compact(context_tokens=150000, context_window=200000)
entry = engine.ledger(action="record", candidates=[{"id": "A", "description": "Option A"}])

# Diagnostics — self-audit
budget = engine.context_budget(context_tokens=150000, context_window=200000)
eval_result = engine.eval_harness(action="run", eval_id="EVAL-1", results=[True, True, False])
rules = engine.rules_distill(action="scan", source_types=["events", "decisions"])
```

See [MCP server](mcp.md) for the full tool reference and [Public API](../reference/public-api.md)
for the Python API.

## Embed in an MCP host (v2.0)

To run Conscio *inside* a host (CLI/IDE/agent), point it at the `conscio-mcp`
stdio server instead of calling the engine directly. The host feeds perception,
reads cognition, and asks for audited proposals — **propose-only**: Conscio signs
and audits the intent, the host executes. See [MCP server](mcp.md).
