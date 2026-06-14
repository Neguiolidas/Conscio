# Conscio

**A self-awareness framework for AI agents.** Conscio gives an agent a persistent,
context-aware inner life — memory, introspection, goal generation, and a metabolic
rhythm — and an *audited* path from thought to action.

Two surfaces, deliberately separated:

- **`reflect()` — passive & advisory.** Reads a world-state string, updates the
  world model, generates goals, and returns a compact state you can inject into a
  prompt. No LLM calls, no side effects, append-only. Always safe.
- **`act()` — opt-in & audited.** Downstream of `reflect()`. Proposes one action,
  runs it through a validated contract + a hostile Skeptic audit + risk gating +
  earned autonomy (TrustMatrix) + a circuit breaker. HIGH-risk actions are always
  queued for a human.

## Install

```bash
pip install conscio
```

Zero-dependency core (only `numpy` + the Python standard library).

## 30-second taste

```python
from conscio import ConsciousnessEngine

with ConsciousnessEngine(model_name="kimi-k2.6") as engine:
    result = engine.reflect(world_state="All systems operational", confidence=0.8)
    print(result["summary"])
    print(engine.get_state_for_injection())
```

From the command line:

```bash
conscio info kimi-k2.6
conscio reflect "All systems operational"
conscio plugins
```

## Where to next

- [Install](guides/install.md) · [Quickstart](guides/quickstart.md)
- [Architecture](guides/architecture.md) — the layered design
- [Plugins & extension points](guides/plugins.md) — adapters, sensors, tools
- [Safety rules](guides/safety-rules.md) — the non-negotiables
- [Public API](reference/public-api.md) — the stable surface
- [Claims ledger](CLAIMS.md) — what Conscio can and cannot prove about itself
