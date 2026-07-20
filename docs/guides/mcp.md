# MCP server (embodiment)

`conscio-mcp` is a hand-rolled, **stdlib-only** [MCP](https://modelcontextprotocol.io)
stdio server (newline-delimited JSON-RPC 2.0). It lets **any** MCP host — a CLI,
an IDE, or an agent — plug into a Conscio instance and consume its cognition as a
live consciousness-layer. Zero new runtime dependency; nothing here opens a
socket.

The surface is **propose-only by default**: Conscio perceives, reflects, recalls,
and **audits** proposed actions, but never executes anything itself — the host
stays sovereign over execution. Since **v2.0.1**, opt-in **audited `act`** is
available behind `--enable-act` (see [Audited act](#audited-act-v201-opt-in)):
Conscio audits + gates + ledgers and returns an execution packet; the host still
pulls the trigger.

## Configure a host

Conscio runs one engine per workspace. Point any MCP host at the `conscio-mcp`
command:

```json
{
  "mcpServers": {
    "conscio": {
      "command": "conscio-mcp",
      "args": ["--storage", "/path/to/workspace/.conscio",
               "--adapter", "ollama:qwen3.5:0.8b"]
    }
  }
}
```

- `--storage` — per-workspace state dir (one engine = one workspace).
- `--adapter` — needed for `propose_action` / `propose_plan` / `act` (the Skeptic
  and Actor call a model). Forms: `mock`, `ollama:<model>`. If omitted, the six
  daemon provider types are also built from `~/.config/conscio/config.json`
  (`lmstudio`/`ollama`/`openai`/`anthropic`/`gemini`/`openai-compat`). Without any
  adapter, read tools still work and `propose_*` / `act` fail closed.
- `--enable-act` (off by default) / `--awake` — opt into audited `act` (see
  [Audited act](#audited-act-v201-opt-in)).
- `--enable-hermes-review --reviewer <id>` (repeatable) — enable review channel.
- `--enable-relay --relay-peer <id>` (repeatable) — enable cross-host relay.
- `--liaison-db <path>` — mailbox path (default `$HERMES_HOME/liaison.db`).
- `--max-frame-bytes` (default `1048576`), `--seen-max-rows` (default `10000`),
  `--seen-max-age-days` (default `30`).

Run `conscio-mcp` directly (the console entry point), not `python -m
conscio.mcp.server`.

## Tools — BASE (always available)

### `conscio.feed(event, session_tokens?)`

Ingest a perception Event → `perceive` + `reflect` → returns the updated advisory.
Idempotent on `event.id`. Optional `session_tokens` (int) drives the metabolic
tier (VITAL / ACTIVE / FATIGUE / CRITICAL) based on real host context usage.

```json
{
  "event": {
    "type": "perception",
    "category": "consciousness",
    "data": {"summary": "user reported interface bug"},
    "ts": 0
  },
  "session_tokens": 8000
}
```

### `conscio.note(event)`

Record a raw Event to the event log (**no reflect**). Idempotent on `event.id`.
Use for fire-and-forget logging — `feed` is the heavier "perceive + reflect" path.

```json
{
  "event": {
    "type": "decision",
    "category": "consciousness",
    "data": {"summary": "[neurata] murmur_hash para content_hash"},
    "id": "design-2026-07-19-001"
  }
}
```

### `conscio.advisory()` (pure read)

Current cognitive state. No args. Returns mode, metabolic tier, active goals,
recent reflections.

### `conscio.recall(query, k?, categories?)` (pure read)

Retrieve relevant past context (FTS5 BM25 + RAG fusion).

```json
{"query": "debug cron Hermes", "k": 5, "categories": ["consciousness", "session"]}
```

### `conscio.propose_action(intent)`

Audit an explicit action intent with the Skeptic. **Never executes.**
Returns `{verdict: "PASS"|"FAIL", reasons, risk_flags, confidence, proposal}`.

```json
{"intent": {"action": "delete", "target": "/tmp/old.log", "reason": "cleanup"}}
```

### `conscio.propose_plan(goal, tools)`

Actor generates ONE action toward `goal`, constrained to the declared `tools`
vocabulary. **Never executes; not free-form; not multi-step.**

```json
{
  "goal": "limpar logs antigos",
  "tools": [{"name": "shell", "description": "run shell command"}]
}
```

### `conscio.state()` (pure read)

ConsciousnessState snapshot. No args.

### `conscio.events(type?, category?, since?, limit?)` (pure read)

Recent events. All args optional.

```json
{"type": "error", "category": "trading", "limit": 20}
```

### `conscio.handoff()` (pure read)

Latest session handoff (markdown). No args.

### `conscio.structure()` (pure read)

Workspace structural graph (consent-gated; data, never code). Returns
`loaded=false` if not consented or not loaded.

### `conscio.structural_lookup(key)` (pure read)

Resolve a structural node / hyperedge / community id from the loaded graph to
its detail. `null` on miss.

```json
{"key": "node:src/lib/auth.py"}
```

### `conscio.cognitive_cycle()` (v2.8)

Run one explicit cognitive pass: reflect → synthesize → propose/act → learn →
self-improve. Returns a report of each stage. The `act` stage only runs when
the server has `--enable-act` on.

## Tools — ACT (opt-in `--enable-act`)

Available only with `--enable-act` (and engine Awake):

- `conscio.act(intent, idempotency_key?)` — execute a pre-audited action
- `conscio.report_result(ledger_id, result)` — feedback the outcome
- `conscio.pending()` — list actions awaiting approval
- `conscio.approve(ledger_id)` — approve a pending action
- `conscio.reject(ledger_id, reason)` — reject a pending action

See [Audited act (v2.0.1)](#audited-act-v201-opt-in) for the full flow.

## Tools — REVIEW (opt-in `--enable-hermes-review`)

Cross-agent `hermes_review` channel. Requires `--reviewer <id>` allowlist.

- `conscio.reviews(limit?)` — inbox of pending review requests
- `conscio.review_approve(request_id, verdict)` — issue verdict PASS + comments
- `conscio.review_reject(request_id, verdict)` — issue verdict FAIL + comments
- `conscio.poll_reviews()` — long-poll inbox for new requests

## Tools — RELAY (opt-in `--enable-relay`)

General cross-agent messaging. Requires `--relay-peer <id>` allowlist.

- `conscio.relay_send(to, type, payload)` — send free-form message
- `conscio.relay_inbox(limit?)` — read inbox
- `conscio.relay_read(ids)` — mark messages read
- `conscio.relay_broadcast(type, payload)` — fan-out to all peers

Reserved-type isolation: `review_request` / `review_verdict` never sent or
surfaced by relay. Payload cap: 64KB. Retention: 7 days after read.

## Resources (read-only URIs)

| URI | Returns |
|---|---|
| `conscio://advisory` | Current advisory (JSON). |
| `conscio://state` | ConsciousnessState snapshot. |
| `conscio://events?type=&category=&since=&limit=` | Recent events. |
| `conscio://handoff` | Latest session handoff (markdown). |

## The Event schema

`feed` and `note` take one rigid `event` object:

| Field | Required | Notes |
|---|---|---|
| `id` | recommended | Idempotency key. If absent, the server derives a stable content hash. |
| `type` | yes | One of `VALID_TYPES` (see below). |
| `category` | yes | One of `VALID_CATEGORIES` (see below). |
| `data` | yes | JSON-serializable payload (the "content"). |
| `ts` | no | Epoch seconds; the server stamps when absent. |

A duplicate `id` returns the **exact prior result** — retries never inflate the
world model or the event log.

## VALID_TYPES (22)

```
tool_call, reflection, trade, error, anomaly, decision, perception,
goal_created, goal_expired, evolution_proposed, system, consciousness,
session, coherence:dissonance, awake:changed, workspace:changed,
structure:changed, proposal:audited, host:event, act:result, reflection_gate
```

Invalid type raises `ValueError` immediately.

## VALID_CATEGORIES

**EventBus (5):** `system`, `trading`, `consciousness`, `external`, `session`

**ContentStore (8):** `reflection`, `perception`, `trading`, `system`, `error`,
`consciousness`, `external`, `session`

Project names like `"neurata"` are NOT valid categories. Use `"consciousness"`
with a `[project-name]` prefix in the summary for grepability.

### Mapping practice

| Workflow | type | category |
|---|---|---|
| Design decision | `decision` | `consciousness` |
| Exception / failure | `error` | `consciousness` (or `system` if infra) |
| External perception | `perception` | `consciousness` |
| Session lifecycle | `session` | `session` |
| Trading signal | `trade` | `trading` |
| Infrastructure event | `system` | `system` |
| Tool was called | `tool_call` | `system` |

## The propose-only loop

1. Host sends perception via `conscio.feed`.
2. Conscio reflects; the host reads `conscio://advisory`.
3. Host asks `conscio.propose_action` (or `propose_plan`) about an intent.
4. Conscio runs the Skeptic and returns a `PASS`/`FAIL` verdict with reasons.
5. **The host** decides and executes.
6. Host feeds the result back as a new Event.

Conscio signs and audits the intent; the host pulls the trigger.

## Audited act (v2.0.1, opt-in)

Off by default. Launch with `conscio-mcp --enable-act` and the engine **Awake**
(`--awake`, or a persisted awake state). Only then do five more tools appear:
`conscio.act`, `conscio.report_result`, `conscio.pending`, `conscio.approve`,
`conscio.reject`.

**The host owns the tools.** Declare a manifest in the `initialize` params — each
entry has a `name`, `params` (the same arg schema the Skeptic validates), a base
`risk` (`low`/`medium`/`high`), and an `approval_policy`
(`auto` / `require_approval` / `hermes_review`):

```jsonc
"params": { "protocolVersion": "2025-06-18",
  "conscio": { "tools": [
    { "name": "deploy", "params": {"env": {"type": "str", "required": true}},
      "risk": "high", "approval_policy": "require_approval" } ] } }
```

**The flow** (Conscio points the weapon and audits it; the host pulls the trigger):

1. Host calls `conscio.act(intent)` with a concrete `{tool, args, rationale,
   expected_outcome}` (optionally an `idempotency_key`). Conscio runs the Skeptic.
2. Skeptic FAIL → `{status: "rejected"}`. PASS + `low`/`medium` + `auto` →
   `{status: "executable", packet, ledger_id}` (claimed). PASS + `high` /
   `require_approval` / `hermes_review` → `{status: "pending_approval"}`. Engine
   asleep or breaker lockdown → `{status: "gated"}`.
3. For a pending action, a human/Hermes calls `conscio.approve(ledger_id)`
   (→ an executable packet) or `conscio.reject(ledger_id, reason)`.
4. **The host executes the packet** — Conscio never does.
5. Host calls `conscio.report_result(ledger_id, result)`; Conscio closes the
   ledger entry, emits an `act:result` event, and feeds the breaker/trust. A
   duplicate report returns `already_reported`.

Every action writes an `ActionLedger` row — the same audited trail native `act()`
uses. `act` is **never** local dispatch: Conscio audits, the host executes.

## Common pitfalls

1. **Invalid `type` / `category`** raises `ValueError` before any DB write. Check
   the valid sets above.
2. **`conscio.note` does NOT run `reflect`** — use `conscio.feed` if you want the
   engine to react. `note` is fire-and-forget.
3. **`event.id`** when present is the **only** idempotency key. A retry with the
   same id returns the prior result (no inflation).
4. **`session_tokens` is only accepted by `feed`**, not by `note`. Without it,
   the metabolic tier is driven by session length heuristics.
5. **`propose_action` and `propose_plan` never execute**, they audit. Only `act`
   (which requires `--enable-act` + Awake) returns an executable packet.
6. **`conscio.relay_*` ignores `review_request` / `review_verdict`** — those only
   travel on the review channel (`reviews` / `review_approve` / `review_reject`).
7. **`structural_lookup` returns `null` on miss** — no exception, just `null`.
8. **Two hosts on the same `$HERMES_HOME` share `liaison.db`** — mailbox is
   filesystem-scoped, not per-instance.
9. **Adapter required for any `propose_*` / `act`** — without `--adapter` these
   fail closed with a clear error. Read tools (`advisory`, `state`, `events`,
   `recall`, `handoff`, `structure`, `structural_lookup`) work without adapter.
10. **`conscio.cognitive_cycle` is explicit** — the daemon's autonomous loop runs
    the same stages, but calling this tool runs **one** pass and returns a report.
    It is not a long-running background loop.
