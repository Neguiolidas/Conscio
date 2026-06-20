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
- `--max-frame-bytes` (default `1048576`), `--seen-max-rows` (default `10000`),
  `--seen-max-age-days` (default `30`).

Run `conscio-mcp` directly (the console entry point), not `python -m
conscio.mcp.server`.

## Tools

| Tool | What it does |
|---|---|
| `conscio.feed(event)` | Ingest a perception Event → `perceive` + `reflect` → returns the updated advisory. Idempotent on `event.id`. |
| `conscio.note(event)` | Record a raw Event to the event log (no reflect). Idempotent on `event.id`. |
| `conscio.advisory()` | Current cognitive state (pure read). |
| `conscio.recall(query, k=3, categories?)` | Relevant past context (FTS5 + RAG). |
| `conscio.propose_action(intent)` | Audit an explicit action `{tool, args, rationale, expected_outcome}` with the Skeptic → `{verdict, reasons, risk_flags, confidence, proposal}`. **Never executes.** |
| `conscio.propose_plan(goal, tools)` | Actor generates ONE action toward `goal`, constrained to the declared `tools` vocabulary (`[{name, description}]`), then the Skeptic audits it. **Never executes; not free-form.** |

## Resources

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
| `type` | yes | e.g. `perception`, `tool_result`, `user_msg`, `log`. |
| `source` | yes | Who/what produced it. |
| `category` | yes | Domain grouping, e.g. `host`, `agent`, `user`. |
| `ts` | no | Epoch seconds; the server stamps when absent. |
| `payload` | yes | The content; numeric fields become signals, the rest observations. |

A duplicate `id` returns the **exact prior result** — retries never inflate the
world model or the event log.

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
