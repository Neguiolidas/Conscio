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

Since **v2.0** ("Connect"), any MCP host — a CLI, an IDE, or an agent — can embed
a Conscio instance over a stdlib-only MCP stdio server (`conscio-mcp`) and consume
its cognition + audited proposals live. The surface is **propose-only**: Conscio
signs and audits intent; the host executes. See [MCP server](guides/mcp.md).

**v3.0** adds 13 ECC tools across three modules: **gates** (ADRs, council,
loop gate, delivery check, investigate), **pipelines** (acceptance criteria,
verify, continuous loop, strategic compact, decision ledger), and
**diagnostics** (context budget, eval harness, rule distillation). All
deterministic, EventBus-backed, and exported from the `conscio` namespace.

**v3.8** ("DeepMiner") adds an agnostic tool-observation store in its own
`obs.db` (SQLite + FTS5), isolated from `conscio.db`: `observe()` captures raw
tool calls fire-and-forget, `recall_observations()` returns an FTS5 snippet
window around each hit, and `compress_observations()` turns a session's most
recent work into a handoff — all deterministic, at **0 LLM tokens**. Measured
on real session transcripts, a recall costs **~110 tokens instead of ~350**,
a median **80% saving** per query at unchanged retrieval fidelity.

**v3.9** ("Context Governor") makes that store automatic and then uses it. On
Claude Code the installer registers hooks that record every tool call into
`obs.db` — fail-open, never altering tool output — and bracket compaction:
`PreCompact` tells the summariser what is worth keeping, `PostCompact` stores
what it produced and points at the detail that survived. `conscio govern` then
measures your stable prefix and where your compactions actually land, prints the
cost curve for every candidate window, and applies the cost-optimal one to the
project's `.claude/settings.local.json` — refusing any window below the floor
your own transcripts show, because a window under the landing point compacts,
lands above its own ceiling, and compacts again. Every measurement comes from
the host's own `message.usage` records rather than from token counting of ours.
**v3.9.7** replaces ChromaDB with three native vector backends — HNSW,
sqlite-vec and numpy — auto-detected at startup, with a one-command
[migration](MIGRATION.md).

**v4.0** makes Conscio installable as a Claude Code plugin and sizes the MCP
surface to the model: `lite` (10 tools), `balanced` (18) or `ultra` (35),
switchable at runtime. A tool list is context the host pays for before the first
prompt — a small model drowns in 35 tools, a large one is crippled by 10. See the
[MCP guide](guides/mcp.md#tool-surfaces) and the
[changelog](https://github.com/Neguiolidas/Conscio/blob/main/CHANGELOG.md).

## Install

```bash
pip install conscio
```

Zero-dependency core (only `numpy` + the Python standard library).

In Claude Code, install it as a plugin instead — memory, capture hooks and 14
slash commands, with no Python toolchain to manage:

```
/plugin marketplace add Neguiolidas/Conscio
/plugin install conscio
```

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
- [MCP server (embodiment)](guides/mcp.md) — embed Conscio in any MCP host, and
  size its [tool surface](guides/mcp.md#tool-surfaces) to the model
- [Safety rules](guides/safety-rules.md) — the non-negotiables
- [Public API](reference/public-api.md) — the stable surface
- [Claims ledger](CLAIMS.md) — what Conscio can and cannot prove about itself
