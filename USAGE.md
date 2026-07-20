# Conscio — Usage Manual

Self-awareness framework for AI agents. 100% local Python + SQLite FTS5. Zero external deps runtime (numpy optional for embeddings).

**Version:** 3.0.0 · **License:** AGPL-3.0-or-later · **Python:** 3.10+

## Install

```bash
pip install conscio
# Or from source:
pip install -e ".[dev]"
```

This installs 6 console scripts:

- `conscio` — main CLI
- `conscio-mcp` — MCP stdio server (the "embodiment" surface)
- `conscio-daemon` — persistent perceive→reflect→act loop
- `conscio-hub` — localhost HTTP control plane
- `conscio-observatory` — read-only state viewer
- `conscio-bench` — inference backend benchmark

## Quickstart — Python API

```python
from conscio import ConsciousnessEngine

# Engine orchestrates everything — ALWAYS close it
with ConsciousnessEngine(model_name="glm-5.2") as engine:
    result = engine.reflect(
        world_state="All systems operational",
        confidence=0.8,
        anomalies=["Unusual latency spike detected"],
    )
    injection = engine.get_state_for_injection()  # bounded by context mode
    hits = engine.recall("latency incidents")
```

## Quickstart — MCP server

Point any MCP host (Claude Code, IDE, agent) at `conscio-mcp`:

```json
{
  "mcpServers": {
    "conscio": {
      "command": "conscio-mcp",
      "args": ["--adapter", "ollama:qwen3.5:0.8b"]
    }
  }
}
```

**Propose-only by default** — Conscio perceives, reflects, recalls, and audits
proposed actions, but never executes. The host stays sovereign over execution.

### Core tools always available

- `conscio.feed(event, session_tokens?)` — perceive + reflect, returns advisory
- `conscio.note(event)` — log raw event (no reflect)
- `conscio.advisory()` — current cognitive state (read-only)
- `conscio.recall(query, k?, categories?)` — retrieve past context (FTS5 + RAG)
- `conscio.state()` — ConsciousnessState snapshot
- `conscio.events(type?, category?, since?, limit?)` — recent events
- `conscio.handoff()` — latest session handoff
- `conscio.structure()` — workspace structural graph (consent-gated)
- `conscio.structural_lookup(key)` — resolve graph node
- `conscio.cognitive_cycle()` — one explicit reflect→synthesize→propose→learn pass

### Propose / Act (act is opt-in via `--enable-act`)

- `conscio.propose_action(intent)` — audit an intent with the Skeptic (never executes)
- `conscio.propose_plan(goal, tools)` — generate ONE audited action toward goal
- `conscio.act(intent)` — return executable packet (host pulls trigger)
- `conscio.report_result(ledger_id, result)` — feedback the outcome
- `conscio.pending()` — pending actions awaiting approval
- `conscio.approve(ledger_id)` / `conscio.reject(ledger_id, reason)`

### Review (opt-in `--enable-hermes-review --reviewer <id>`)

Cross-agent review channel: `conscio.reviews`, `conscio.review_approve`,
`conscio.review_reject`, `conscio.poll_reviews`.

### Relay (opt-in `--enable-relay --relay-peer <id>`)

Cross-agent messaging: `conscio.relay_send`, `conscio.relay_inbox`,
`conscio.relay_read`, `conscio.relay_broadcast`. Reserved-type isolation from
review channel. Payload cap 64KB, retention 7 days after read.

### Resources (read-only URIs)

- `conscio://advisory`
- `conscio://state`
- `conscio://events?type=&category=&since=&limit=`
- `conscio://handoff`

## Event schema

`feed` and `note` take one `event` object:

```json
{
  "id": "optional-idempotency-key",
  "type": "perception",
  "category": "consciousness",
  "data": {"summary": "what happened"},
  "ts": 0
}
```

Fields: `id` (recommended — idempotency key), `type` (required),
`category` (required), `data` (required — JSON-serializable payload),
`ts` (optional — epoch seconds, server stamps when absent).

A duplicate `id` returns the exact prior result — retries never inflate
the world model or the event log.

## VALID_TYPES (must match exactly or ValueError)

```
tool_call reflection trade error anomaly decision perception
goal_created goal_expired evolution_proposed system consciousness
session coherence:dissonance awake:changed workspace:changed
structure:changed proposal:audited host:event act:result reflection_gate
adr:proposed adr:accepted council:convened gate:vetoed
pipeline:acceptance pipeline:verified pipeline:compact pipeline:ledger
diagnostic:budget diagnostic:eval diagnostic:rule
```

## VALID_CATEGORIES

**EventBus (5):** `system`, `trading`, `consciousness`, `external`, `session`

**ContentStore (8):** adds `reflection`, `perception`, `error`

Project names like `"neurata"` are NOT valid categories. Use `"consciousness"`
with a `[project-name]` prefix in the summary/data.

## CLI

```bash
conscio version
conscio info                       # model, context window, mode, budget
conscio reflect                    # one offline reflection cycle
conscio plugins                    # list adapters/sensors/tools
conscio consent                    # workspace structural consent
conscio structure                  # drift + freshness (read-only)
conscio awake                      # enter R9 (autonomous)
conscio sleep                      # leave R9
conscio trial <path>               # trial quarantined skill
conscio promote <path>             # promote trialed skill
conscio init                       # interactive installer (per-host space)
conscio bench --help               # inference benchmark
conscio-daemon --awake             # persistent heartbeat
conscio noosphere --help           # cross-instance skill sharing
conscio-hub --enable-daemon-control
conscio-observatory
```

## Context Modes (auto-detected)

| Mode | Context | Budget | What's injected |
|---|---|---|---|
| Minimal | < 128k | 200 tok | Summary only |
| Compact | 128k–256k | 500 tok | Summary + reflection + top goals |
| Standard | 256k+ | 1000 tok | Full state + world subgraph |

Override via `~/.config/conscio/config.json`:
```json
{"models": {"mimo-v2.5-pro": {"context_window": 1048576}}}
```
Or env: `CONSCIO_CONTEXT_WINDOW=1048576`.

## DB

- Default: `~/.conscio/data/conscio.db` (SQLite WAL + FTS5)
- Per-host: `~/.conscio/instances/<slug>/`
- Override: `CONSCIO_DATA_DIR`
- Vault (API keys): `CONSCIO_VAULT_DIR` (no fallback to global)

## Python modules — common APIs

```python
from conscio import ConsciousnessEngine
from conscio.content_store import ContentStore
from conscio.event_bus import EventBus
from conscio.metabolic import MetabolicContext
from conscio.workspace import WorkspaceContext, EnvClass
from conscio.perception.host_sensor import HostSensor
from conscio.perception.agent_sensor import AgentSensor

# ContentStore: index(label, content, category) — first arg is label, NOT source
with ContentStore() as store:
    store.index(label="auth-bug", content="recursion fix", category="error")
    results = store.search("recursion", limit=5)

# EventBus: emit() returns int (event_id); query() to retrieve
with EventBus() as bus:
    eid = bus.emit("error", "trading", {"pattern": "API timeout"})
    events = bus.query(category="trading", limit=10)

# MetabolicContext.assess is static
state = MetabolicContext.assess(used_tokens=3000, context_window=10000)
state.name  # "VITAL" | "ACTIVE" | "FATIGUE" | "CRITICAL"

# Engine lifecycle
engine = ConsciousnessEngine(model_name="glm-5.2")
engine.wake()  # R9 on
engine.sleep() # R9 off
engine.awake   # → bool
engine.health_check()  # → dict
engine.close()  # ALWAYS — or use with statement

# Opt-in features
ConsciousnessEngine(adaptive_reflection=True, max_reflection_cycles=3)
engine.attach_adapter(intercept_enabled=True)
```

## Top pitfalls

1. **Engine must be closed** — always use `with` or `try/finally close()`. WAL
   grows without checkpoint otherwise.
2. **`conscio.note` doesn't reflect** — `feed` does. `note` is fire-and-forget.
3. **`type` / `category` must be valid** — `ValueError` otherwise. See lists above.
4. **ContentStore first arg is `label`, not `source`** — common mistake.
5. **EventBus.emit() returns int (event_id), not Event** — use `query()` to
   retrieve Event objects with `.is_duplicate` attribute.
6. **TokenTracker.record() takes text, not ints** — raw/filtered strings.
7. **MetabolicContext.assess() is static** — no `get_metabolic_advice()`.
8. **Daemon doesn't attach adapter** — perceive→reflect only. Full loop needs a
   wrapper that calls `engine.attach_adapter(adapter)`.
9. **Sensors are in separate files**: `conscio.perception.host_sensor`, not
   `conscio.perception.sensor`.
10. **`reflect()` is advisory (read-only)**. `act()` / `dispatch()` is executive.
    Never merge these — architectural rule #1.

## Where to read more

- `docs/guides/mcp.md` — full MCP server reference (all tools, flags, examples)
- `docs/guides/quickstart.md` — Python API quickstart
- `docs/guides/install.md` — installation details
- `docs/guides/integration.md` — host agent integration patterns
- `docs/reference/conscio_functions.md` — every public function documented
- `docs/reference/public-api.md` — stable public API surface
- `CHANGELOG.md` — version history
- Repo: https://github.com/Neguiolidas/Conscio
- Issues: https://github.com/Neguiolidas/Conscio/issues
