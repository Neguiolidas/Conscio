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
