# Conscio — Usage Manual

Self-awareness framework for AI agents. Local-first Python + SQLite FTS5.
One runtime dependency (`numpy`); everything else is the standard library.

**Version:** see [CHANGELOG](CHANGELOG.md) · **License:** AGPL-3.0-or-later · **Python:** 3.10+

## Install

In Claude Code, install the plugin — memory, capture hooks and 14 slash
commands, with no Python toolchain to manage:

```
/plugin marketplace add Neguiolidas/Conscio
/plugin install conscio
```

As a library or CLI:

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

### v3.1 — Auto-detect (recommended for LM Studio)

The MCP JSON stays **fixed forever** — Conscio auto-detects what's loaded in
LM Studio and falls back to the next model if the current one fails:

```json
{
  "mcpServers": {
    "conscio": {
      "command": "conscio-mcp",
      "args": ["--model", "auto", "--base-url", "http://localhost:1234/v1"]
    }
  }
}
```

On boot: `GET /v1/models` → filter embedding models → test each chat model →
use first that responds → persist to `~/.config/conscio/config.json`. Runtime:
`FallbackAdapter` switches models on failure automatically.

### Explicit model (any OpenAI-compatible endpoint)

```json
{
  "mcpServers": {
    "conscio": {
      "command": "conscio-mcp",
      "args": ["--model", "liquid/lfm2.5-1.2b", "--base-url", "http://localhost:1234/v1"]
    }
  }
}
```

### Legacy adapter syntax

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

### Tool surfaces — `--mode lite|balanced|ultra`

Every advertised tool schema costs the host context before the first prompt
(~3100 tokens at `ultra`). Three nested surfaces size the list to the model:

| Surface | Tools served | Advertised schema |
|---|---|---|
| `lite` | 10 | ~570 tokens, descriptions flattened to ≤120 chars |
| `balanced` | 18 | ~1520 tokens |
| `ultra` (default) | 35 | ~3100 tokens |

`lite` — `advisory`, `events`, `feed`, `health`, `intercept`, `mode`, `note`,
`recall`, `remember`, `state`. `balanced` adds `context_budget`, `council`, `decide`,
`handoff`, `kg_query`, `recall_observations`, `verify`, `wings_search`.

Precedence is `--mode` > the persisted choice (`<storage>/mcp_mode`) > default.
`conscio_mode` switches at runtime and is present in every surface — in `lite` it
is the only way back out. Filtering applies to the base set only, so a
flag-enabled tool (act, review, relay) is never removed, and an unadvertised tool
is still callable by name through `tools/call`.

### Core tools (in every surface unless noted)

- `conscio_feed(event, session_tokens?)` — perceive + reflect, returns advisory
- `conscio_note(event)` — log raw event (no reflect)
- `conscio_advisory()` — current cognitive state (read-only)
- `conscio_recall(query, k?, categories?)` — retrieve past context (FTS5 + RAG +
  optional vector, auto-detected via sentence_transformers (override with `CONSCIO_VECTORS=0`))
- `conscio_state()` — ConsciousnessState snapshot
- `conscio_events(type?, category?, since?, limit?)` — recent events
- `conscio_handoff()` — latest session handoff
- `conscio_structure()` — workspace structural graph (consent-gated)
- `conscio_structural_lookup(key)` — resolve graph node
- `conscio_cognitive_cycle()` — one explicit reflect→synthesize→propose→learn pass

### Propose / Act (act is opt-in via `--enable-act`)

- `conscio_propose_action(intent)` — audit an intent with the Skeptic (never executes)
- `conscio_propose_plan(goal, tools)` — generate ONE audited action toward goal
- `conscio_act(intent)` — return executable packet (host pulls trigger)
- `conscio_report_result(ledger_id, result)` — feedback the outcome
- `conscio_pending()` — pending actions awaiting approval
- `conscio_approve(ledger_id)` / `conscio_reject(ledger_id, reason)`

### Review (opt-in `--enable-hermes-review --reviewer <id>`)

Cross-agent review channel: `conscio_reviews`, `conscio_review_approve`,
`conscio_review_reject`, `conscio_poll_reviews`.

### Relay (opt-in `--enable-relay --relay-peer <id>`)

Cross-agent messaging: `conscio_relay_send`, `conscio_relay_inbox`,
`conscio_relay_read`, `conscio_relay_broadcast`. Reserved-type isolation from
review channel. Payload cap 64KB, retention 7 days after read.

### v3.3 — Gate tools

- `conscio_decide(question, options)` — structural decision with ADR
- `conscio_council(question)` — 3-voice deterministic review (architect, skeptic, pragmatist)
- `conscio_loop_gate(world_state)` — act/block gate: last reflection, rationalization scan, proposal freshness
- `conscio_delivery_check()` — verifies blockers, staleness, rationalization before shutdown
- `conscio_investigate(topic)` — hypothesis scan over recent events

### Resources (read-only URIs)

- `conscio://advisory`
- `conscio://state`
- `conscio://events?type=&category=&since=&limit=`
- `conscio://handoff`

## DeepMiner — agnostic tool observation (v3.8)

Capture raw tool calls into an isolated `obs.db` (SQLite + FTS5) and turn them
into a searchable handoff — all at **0 LLM tokens**, separate from `conscio.db`.

```python
from conscio.engine import ConsciousnessEngine

eng = ConsciousnessEngine("glm-5.1")
eng.set_session("session-123")             # share the platform session id

# fire-and-forget capture — never raises, never blocks (returns obs id, or -1)
eng.observe("edit_file", "fix auth bug", "done", project="/home/me/proj")

# full-text recall over raw tool calls (query bound as a literal FTS phrase).
# input/output carry a snippet window around the hit, not the whole row.
hits = eng.recall_observations("auth", k=5)
hits = eng.recall_observations("auth", k=5, full=True)   # whole row instead

# compress the session into a handoff (0 tokens); persisted via the content
# store — never touches the platform-owned _session_handoff.md
result = eng.compress_observations()       # {"handoff": ..., "count": N, "session_id": ...}
eng.close()
```

Since **v3.9** capture is full-fidelity, not clipped: `observe()` stores each
field whole up to `MAX_FIELD_BYTES` (1 MiB), against the 1024 chars the v1 schema
kept. Rows migrated from v1 stay clipped — the detail they lost was never
written. `count` is everything the session did; the handoff itself carries the
most recent observations that fit its char budget — a handoff exists to resume
work, so the newest calls win.

> Because capture is complete, anything a tool reads or writes — including
> secrets — can land in `obs.db`. It is a second copy of what the host already
> keeps in its transcripts, with a 30-day retention window.

As MCP tools (any agent can call them — required arg in **bold**):

```jsonc
{"name": "conscio_observe",
 "arguments": {"tool": "edit_file", "input": "fix auth", "output": "done", "project": "/proj"}}

{"name": "conscio_recall_observations",
 "arguments": {"query": "auth", "k": 5, "full": false}}
```

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

**ContentStore (11):** adds `reflection`, `perception`, `error`, `pentest`, `reference`, `payload`

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
conscio ingest <path>               # bulk-index a directory into ContentStore
                                    # (--category --chunk-size --overlap --model --storage)
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

- Default: `~/.hermes/consciousness/conscio.db` (SQLite WAL + FTS5)
- Tool observations: `~/.hermes/consciousness/obs.db` (separate store)
- Cross-instance state (KG, hallways, vectors, handoff, sandbox): `~/.conscio/`
- Per-host: `~/.conscio/instances/<slug>/`
- Override: `storage_path=` / `--storage`; the CLI and daemon also read
  `$HERMES_HOME` (the library default does not)
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

# bulk directory ingest with semantic chunking (CLI: conscio ingest <path>)
engine.ingest_directory("./docs", category="reference")

# vector search auto-detects sentence_transformers; override: CONSCIO_VECTORS=0

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
2. **`conscio_note` doesn't reflect** — `feed` does. `note` is fire-and-forget.
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
11. **Vector search is auto-detected** — if `sentence_transformers` is available,
    vectors auto-enable; set `CONSCIO_VECTORS=0` to force FTS5-only (existing
    installs without the dep don't silently start embedding on every `index()` call).

## Memory modules (KG, Hallways, Embeddings, Miner, Migration)

```python
from conscio import (
    KnowledgeGraph, Hallways, WingManager, VectorBackend,
    Deduplicator, EntityDetector, EmbeddingProvider, Miner,
    export_archive, import_archive, import_format_mempalace,
)

# KnowledgeGraph — entities + triples
kg = KnowledgeGraph(db_path="kg.db")
kg.add_entity("Conscio", entity_type="project")
kg.add_entity("Hermes", entity_type="project")
kg.add_triple("Conscio", "integrates_with", "Hermes")
ent = kg.query_entity("Conscio")
rels = kg.query_relationship("Conscio")
kg.close()

# Hallways — wing/room/drawer hierarchy
hw = Hallways(db_path="hw.db")
hw.create_wing("projects")
hw.create_room("projects", "pentest")
hw.create_drawer("projects", "pentest", label="vault_scan")
hw.close()

# WingManager — Hallways + ContentStore integration
from conscio import ContentStore
cs = ContentStore(db_path="cs.db")
wm = WingManager(hallways_db="hw.db", content_store=cs)
wm.index(label="report", content="Pentest vault.grolv.com.br",
         category="external", content_type="prose",
         wing="projects", room="pentest")
results = wm.search("pentest vault", wing="projects", limit=5)
wm.close()

# EntityDetector — regex Unicode (PT accents supported)
ed = EntityDetector(kg=kg)
found = ed.detect_and_store("Samuel released Conscio v3.2.0 at vault.grolv.com.br")
# → detects: Samuel, Conscio, v3.2.0, vault.grolv.com.br

# EmbeddingProvider — native fallback (no daemon needed)
ep = EmbeddingProvider()
if ep.available():
    vec = ep.embed("Conscio consciousness framework")
    # 384-dim by default (all-MiniLM-L6-v2)
    # 768-dim optional: CONSCIO_EMBED_MODEL=nomic-embed-text-v1.5

# Miner — file + conversation ingestion
m = Miner(wing_manager=wm)
m.ingest_file("report.md", wing="projects", room="pentest")
m.ingest_directory("./docs", wing="docs", room="general")

# Migration — export/import
export_archive("backup.tar.gz", content_store=cs, kg=kg, hallways=hw)
cs2, kg2, hw2 = import_archive("backup.tar.gz", target_dir="./restored")

# MemPalace adapter
count = import_format_mempalace("~/.mempalace/palace", wing_manager=wm)
```

### Embedding configuration

Default: `all-MiniLM-L6-v2` (384-dim, ~90MB, native in-process).

Optional 768-dim model:
```bash
export CONSCIO_EMBED_MODEL=nomic-embed-text-v1.5
export CONSCIO_EMBED_DIM=768
```

Fallback chain: Ollama → OpenAI-compatible API → sentence_transformers (native) → None.

## When to call Conscio (MCP trigger rules)

Conscio is a cognitive refinement layer, not a fact database. Calling it on
every message wastes tokens and adds latency. These rules prevent that.

### CALL Conscio when the cost of being wrong is high

| Scenario | Tool | Why |
|---|---|---|
| Pentest / security audit | `feed` + `cognitive_cycle` | Systematic coverage, no missed vectors |
| Architectural decision | `decide` or `council` | Structured ADR, multi-voice review |
| Debugging (investigate) | `investigate` | Hypothesis scan over recent events |
| Multi-step delivery | `loop_gate` + `delivery_check` | Block before acting on stale/rationalized plans |
| Self-review of output | `evaluate` | 5-axis rubric (accuracy, completeness, clarity, actionability, conciseness) |
| High-risk irreversible action | `council` | 3-voice review before committing |

### DO NOT call Conscio for

- Factual lookup (use `web_search` or `recall` directly)
- Casual conversation
- Simple mechanical tasks (file copy, git add)
- One-shot tool calls
- Tasks with no decision or judgment involved

### Criterion

The decision rule is simple: **cost of reversal**. If undoing a wrong decision
is cheap (rename a variable, fix a typo), Conscio adds overhead without value.
If undoing is expensive (architectural lock-in, security exposure, multi-step
delivery with no checkpoint), Conscio pays for itself.

## Awake Mode with sensors (v3.3)

The daemon's cognitive cycle now pauses when no sensor produces signal — it does
not burn tokens spinning on empty perception. Two sensors ship with v3.3:

```python
from conscio.perception import FilesystemSensor, GitSensor

# Watch a directory tree for mtime changes (created/modified/deleted)
fs = FilesystemSensor("/path/to/project", depth=3, max_files=50)

# Watch a git repo for new commits (idempotent by hash)
git = GitSensor("/path/to/repo", timeout=5.0)

# Plug into daemon
from conscio.daemon import Daemon
daemon = Daemon(engine=engine, sensors=[fs, git], ...)
daemon.run()  # perceives only when something changes
```

Both sensors are read-only (`Risk.LOW`), never raise, and degrade to empty
frames on errors (missing dir, no git binary, permission denied).

### Goal generation (no LLM)

When sensors detect changes, `GoalTemplates` maps signals to concrete goals
deterministically:

- `.py` file modified → "verificar se testes cobrem {file}"
- New commit → "revisar diff {hash}"
- > 4 files/commits → grouped summary
- Test files modified → skipped (meta-recursion guard)

```python
from conscio.awake import goals_from_world_state

goals = goals_from_world_state(world_state)
# → ["verificar se testes cobrem /repo/src/app.py"]
```

### Neurata bridge (optional)

If [Neurata](https://github.com/Neguiolidas/Neurata) is installed and in PATH,
Conscio can query it for skill/capability inventory:

```python
from conscio.integrations import NeurataBridge

bridge = NeurataBridge()
if bridge.available:
    result = bridge.query("firebase config")
    # → {"ok": True, "results": [...]}
```

Without Neurata: `available=False`, all methods return `None`. Zero impact on
Conscio operation.

## Where to read more

- `docs/guides/mcp.md` — full MCP server reference (all tools, flags, examples)
- `docs/guides/quickstart.md` — Python API quickstart
- `docs/guides/install.md` — installation details
- `docs/guides/integration.md` — host agent integration patterns
- `docs/reference/public-api.md` — stable public API surface
- `CHANGELOG.md` — version history
- Repo: https://github.com/Neguiolidas/Conscio
- Issues: https://github.com/Neguiolidas/Conscio/issues
