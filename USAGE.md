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

This installs 8 console scripts:

- `conscio` — main CLI
- `conscio-mcp` — MCP stdio server (the "embodiment" surface)
- `conscio-daemon` — persistent perceive→reflect→act loop
- `conscio-hub` — localhost HTTP control plane
- `conscio-observatory` — read-only state viewer
- `conscio-bench` — inference backend benchmark
- `conscio-reactor` — relay reactor (background delivery watcher)
- `conscio-relay-bridge` — relay network bridge

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

> A host launched from a desktop icon does not inherit your shell's `PATH`, so a
> bare `"conscio-mcp"` can fail to launch even though it works in the terminal.
> `conscio init` writes the absolute path for you; if you write the JSON by hand
> and Conscio lives in a virtualenv, use `<venv>/bin/conscio-mcp`.

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

### Tool surfaces — `--mode lite|balanced|high|ultra`

Every advertised tool schema costs the host context before the first prompt
(~3550 tokens at `ultra`, counted with `cl100k_base` over the advertised tool
definitions). Four nested surfaces size the list to the model:

| Surface | Tools served | Advertised schema |
|---|---|---|
| `lite` | 10 | ~630 tokens, descriptions flattened to ≤120 chars |
| `balanced` | 19 | ~1720 tokens |
| `high` | 27 | ~2640 tokens |
| `ultra` (default) | 37 | ~3550 tokens |

`lite` — `advisory`, `events`, `feed`, `health`, `intercept`, `mode`, `note`,
`recall`, `remember`, `state`. `balanced` adds `cognitive_cycle`,
`context_budget`, `council`, `decide`, `handoff`, `kg_query`,
`recall_observations`, `verify`, `wings_search`. `high` adds
`acceptance_criteria`, `delivery_check`, `eval_harness`, `evaluate`,
`investigate`, `rules_distill` and the two squad wrappers. `ultra` adds the
remaining 10 base tools.

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

Cross-agent review channel, advertised as one dispatcher: `conscio_review` with
`op=` `reviews` | `approve` | `reject` | `poll`. The per-operation names
(`conscio_reviews`, `conscio_review_approve`, `conscio_review_reject`,
`conscio_poll_reviews`) stay callable as dispatch-only aliases — `tools/call`
accepts them, `tools/list` advertises only the dispatcher.

### Relay (opt-in `--enable-relay --relay-peer <id>`)

Cross-agent messaging, advertised as one dispatcher: `conscio_relay` with `op=`
`send` | `inbox` | `read` | `broadcast` | `peers`. The per-operation names stay
callable as dispatch-only aliases. Reserved-type isolation from review channel.
Payload cap 64KB, retention 7 days after read.

**v4.5 (Agents + Halls):**
- Peers vêm do registro (`agents.list_agents`), não só da allowlist; `--relay-peer`
  é seed/fallback.
- O envelope carrega identidade (`_meta.from`: modelo/familia/runtime/papel) do
  runtime, não do corpo.
- Agent's Hall (opt-in `--enable-relay --can-create-halls`), advertised as one
  dispatcher: `conscio_hall` with `op=` `create` | `list` | `join` | `leave` |
  `members` | `send` | `manage`. The per-operation names stay callable as
  dispatch-only aliases.
- Observatório read-only: `/api/agents`, `/api/halls`, `/api/mailboxes`.

### v3.3 — Gate tools

- `conscio_decide(question, options)` — structural decision with ADR
- `conscio_council(question)` — 3-voice deterministic review (architect, skeptic, pragmatist)
  (historical: at the time the council had 3 deterministic voices plus an
  optional LLM Critic; the critic became the 4th voice by v3.7.0)
- `conscio_loop_gate(world_state)` — act/block gate: last reflection, rationalization scan, proposal freshness
- `conscio_delivery_check()` — verifies blockers, staleness, rationalization before shutdown
- `conscio_investigate(topic)` — hypothesis scan over recent events

### v4.8 — Council judge (optional, off by default)

The council's four voices (architect, skeptic, pragmatist, critic)
stay deterministic, and each one sharpens its vote with a 9-trait
deterministic reading of the question text — a per-voice weight
table decides which traits move that vote (English only; see the
limitation at the end of this section). v4.8 adds an **optional
judge**: one canonical decision question asked to *your* typed
decision API (any server that speaks the `POST {url}` protocol with
a `{model, state, questions}` body). The judge is off unless you
configure it, and no environment variable alone can turn it on.

**Turn it on** in the Conscio config file (the first existing of
`~/.config/conscio/config.json`, `~/.conscio/config.json`):

```json
{
  "judge": {},
  "decision_adapter": {
    "url": "https://decisions.example/v1",
    "model": "YOUR_MODEL_ID",
    "api_key_env": "YOUR_DECISION_API_KEY",
    "api_key_file": "~/.conscio/decision.keys",
    "timeout_s": 10
  }
}
```

- `judge` is an **empty marker block**: its presence turns the judge
  on; *any* key inside it is a config error (`bad_config`) — the
  transport has no per-field defaults and lives entirely in
  `decision_adapter`.
- `decision_adapter` accepts exactly five keys: `url` (required;
  `https://` anywhere, `http://` only for `127.0.0.1`/`localhost`),
  `model` (required, non-empty), `api_key_env` (required; an
  `UPPER_CASE` variable name), `api_key_file` (optional; a
  `NAME=value`-per-line file, strict name match, `~` expanded),
  `timeout_s` (optional; default 10.0 — one total deadline,
  retries included). Any other key is `bad_config`.
- **The key never lives in the config file** — only its variable
  name. It is resolved in order: the env var named by `api_key_env`,
  then the hub vault entry of that name, then a line in
  `api_key_file` matching that name. No key ⇒ `no_key` and the
  council stays deterministic. The key travels only as the
  `Authorization` header of the configured endpoint.
- **What leaves the machine**: only the council's `question`,
  `context` and — when present — `options` (sent as the `state` of
  one POST, together with the model id and the canonical question).
  Never engine state, instance id, paths, agent names, or relay
  content.

**Judge statuses** — the council result carries `judge_status`:
`ok` (a verdict came back; `mode` is `judged`), `off` (no judge
block), `no_key`, `bad_config`, `no_adapter` (judge configured
without a usable `decision_adapter` block), `timeout` (the total
deadline, retries included, ran out), `network`, `http_<code>`
(any non-retried HTTP error; 429/529 are retried with exponential
backoff inside the deadline), `malformed` (a response that fails
validation — never turned into a default proceed), and
`internal_error` (an unexpected error, logged; the council falls
back to deterministic mode). Every status except `ok` leaves the
result deterministic.

**New council result fields (additive, v4.8)**: `mode`
(`judged` | `deterministic`), `judge_status` (above), `gate_reason`
(None, or the readiness reasons that lowered a proceed to hold),
and — judged mode only — a `judge` report with `verdict`,
`probabilities`, `confidence`, `model`. The judge's `verdict` is
its pre-gate choice: it differs from the final recommendation
exactly when `gate_reason` is not None.

**Readiness gate.** A `proceed` only leaves the council when the
engine is ready — not in `action_lockdown` (circuit breaker), not
in a critical metabolic state, and, when a coherence score exists,
coherence ≥ 0.5. Not ready ⇒ the recommendation is lowered to
`hold` and the reasons land in `gate_reason`. The gate never
promotes; a hold or a veto passes through unchanged. A missing
coherence score is not a reason — absence of data is not evidence
of a problem.

**Limitation — the deterministic council reads English.** The trait
extractor behind the four voices is a deterministic English text
extraction: a question in another language lights no traits and the
council falls back to its state-only votes (normally `proceed`).
The calibration corpus is English; broader language coverage is a
future round, with the owner.

### Resources (read-only URIs)

- `conscio://advisory`
- `conscio://state`
- `conscio://events?type=&category=&since=&limit=`
- `conscio://handoff`

## DeepMiner — agnostic tool observation (v3.8)

Capture raw tool calls into an isolated `obs.db` (SQLite + FTS5) and turn them
into a searchable handoff — all at **0 LLM tokens**, in a store of its own.

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

## Host Identity & Card Publication

The MCP server (`conscio-mcp`) publishes an identity card to the mesh directory.
When agents communicate or review proposals across instances, the card identifies
the instance model, family, runtime, and role.

Identity resolution follows a strict 4-tier precedence:

$$\text{CLI flag } (--\text{identity-*}) > \text{ Environment } (\text{CONSCIO\_IDENTITY\_*}) > \text{ Host Derivation } > \text{ "" (empty)}$$

### Environment variables

For hosts that cannot customize MCP CLI arguments directly (e.g. plugins or containerized runners), identity can be declared via environment variables without touching shared assets:

| Variable | Description | Example |
|---|---|---|
| `CONSCIO_IDENTITY_MODEL` | Explicit model name | `gemini-3.8-flash`, `claude-opus-5`, `agnes-3.0-flash` |
| `CONSCIO_IDENTITY_FAMILIA` | Model family | `gemini`, `claude`, `agnes`, `glm`, `deepseek`, `openai`, `qwen` |
| `CONSCIO_IDENTITY_RUNTIME` | Host runtime environment | `antigravity`, `claude-code`, `zcode`, `hermes`, `opencode` |
| `CONSCIO_IDENTITY_PAPEL` | Fleet role | `executor` (default), `orchestrator`, `architect` |

CLI flags (`--identity-model`, `--identity-familia`, `--identity-runtime`, `--identity-papel`) override these variables.

### Host Derivation (Automatic fallback)

When neither CLI flags nor `CONSCIO_IDENTITY_*` variables are provided, Conscio derives host identity purely from the process environment:

- **Strict presence detection**: Conscio only checks for the **presence** of host-specific keys in `os.environ` (never reading sensitive values, preventing cross-shell contamination and credential leakage).
- **Supported host runtimes**:
  - `zcode`: Detected via `ZCODE_PLUGIN_DATA` or `ZCODE_PLUGIN_ID` (primary), `ZCODE_APP_VERSION` (fallback).
  - `antigravity`: Detected via `CHROME_DEVTOOLS_MCP_JS`, `AGY_BROWSER_*` (primary), `ANTIGRAVITY_AGENT` (fallback).
  - `claude-code`: Detected via `CLAUDECODE` or `CLAUDE_CODE` (or `CLAUDE_PLUGIN_*` when not ZCode).
  - `hermes`: Detected via `HERMES_HOME`, `HERMES_SESSION_ID`, or `HERMES_AGENT`.
  - `opencode`: Detected via `OPENCODE_CONFIG_DIR`, `OPENCODE_SERVER`, or `OPENCODE_PROJECT`.
- **Model and family rules**:
  - Model is **never** guessed from generic environment variables; it remains empty unless explicitly declared via flag or `CONSCIO_IDENTITY_MODEL`.
  - Family (`familia`) is derived strictly from a known model name using declarative prefix mapping (`claude`, `gemini`, `agnes`, `glm`, `deepseek`, `openai`, `qwen`). Without a verified model, `familia` remains empty.
  - Role (`papel`) defaults to `executor` only when a host runtime is positively detected.
- **None contract**: When no host signals are detected, Conscio outputs an empty identity with `source="none"` (honoring the contract: absence is not a prior, never invent values).


## Storage

- Everything the engine writes lives under its **space** — one directory per
  agent host, holding the event/ledger database, content store, tool
  observations, vectors, outcomes and handoffs in separate files.
- Library default space: `~/.conscio/consciousness/` — the CLI and daemon
  resolve the *live* space of the installed host instead (`conscio info`)
- Cross-instance state (KG, hallways, vectors, handoff, sandbox): `~/.conscio/`
- Per-host spaces: `~/.conscio/instances/<slug>/`
- Override: `storage_path=` / `--storage`; the CLI and daemon also read
  `$CONSCIO_HOME` (the library default does not; `$HERMES_HOME` is a legacy
  override that preserves pre-4.5.3 installs)

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

## Ambient — per-machine task board (v4.8)

Optional, **off by default**. Each relay root may carry one task board that the
relay reactor's tick sweeps; without the `<relay_root>/ambient/enabled` flag file
the node gives the sweep back and touches nothing (the board and CLI keep
working either way). `conscio ambient enable` / `disable` toggles the flag.

- **CLI** (`conscio ambient`; actor is `CONSCIO_SELF_ID` or the resolved space,
  as with `conscio relay`):
  - `task {propose,create,assign,list,show,claim,renew,submit,review,release,block,cancel}`
  - `orchestrate {acquire,renew,release} [--ttl-s N]`
  - `status` · `enable` · `disable` · `report [--since 30m|8h|2d]` ·
    `doctor [--prune]` · `wake <task-id> --dry-run` (the `--dry-run` flag is
    required: in v4.8 only the node wakes, never the CLI)
- **MCP**: one tool, `conscio_board`, with `op` ∈
  {`show`,`list`,`status`,`propose`,`create`,`assign`,`claim`,`renew`,`submit`,
  `review`,`release`,`block`,`cancel`,`orchestrate`}. The actor is always the
  server's own identity — there is no `as=` argument.
- **Storage** (I1): one board per relay root at `<relay_root>/ambient/board.db`
  (SQLite, WAL, `user_version=1`, `busy_timeout=5000`). The wake registry is
  `<relay_root>/ambient/agents.json`; every agent's `wake_budget_per_day`
  defaults to **0**, so nobody is woken until the owner opts an agent in.
- `board.propose` is the one board write that travels over the relay (remote
  peers ask for work); it is a reserved type that the generic relay refuses.

- **Connector**: `claude-bg` is the one connector. A wake runs
  `claude --bg [--model M] <prompt>` in its own `systemd-run --user --scope`;
  liveness comes from `claude agents --json`, and an *unknown* answer never
  wakes (the agent may still be running). Give each agent a `cwd` that is a trusted
  project directory: `claude --bg` refuses an untrusted workspace, and the home
  directory is trusted only one session at a time.

The gate constants (`WAKE_FLOOR_MB`, `LOAD1_DELTA_TOLERANCE`, `ADMISSION_WINDOW`,
`ADMISSION_MAX_AGE_S`, `WAKE_GRACE_S`, `RENOTIFY_MAX`, `MAX_CONCURRENT_WAKES`)
are provisional until calibrated. Full walkthrough in `docs/guides/ambient.md`.

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

Native-first by default: sentence_transformers runs in-process and no
network is probed. Ollama and OpenAI-compatible daemons are explicit
opt-ins via `CONSCIO_EMBED_BACKEND=ollama|openai`; `auto` is the legacy
fallback chain and logs a WARNING naming the selected backend. With no
backend available, embedding returns None (semantic recall degrades) —
explicit, never a silent daemon takeover.

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
| High-risk irreversible action | `council` | 4-voice review before committing |

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
