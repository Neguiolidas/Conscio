# MCP server (embodiment)

`conscio-mcp` is a hand-rolled, **stdlib-only** [MCP](https://modelcontextprotocol.io)
stdio server (newline-delimited JSON-RPC 2.0). It lets **any** MCP host — a CLI,
an IDE, or an agent — plug into a Conscio instance and consume its cognition as a
live consciousness-layer. Zero new runtime dependency; nothing here opens a
socket.

In v2.0.0 the surface is **propose-only**: Conscio perceives, reflects, recalls,
and **audits** proposed actions, but never executes anything itself. The host
stays sovereign over execution. (Audited execution — `act` — lands in v2.0.1 with
a host-execution callback model.)

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
- `--adapter` — only needed for `propose_action` / `propose_plan` (the Skeptic and
  Actor call a model). Forms: `mock`, `ollama:<model>`. Without it, read tools
  still work and `propose_*` fail closed with `verdict: FAIL, reasons:["no
  adapter attached"]`.
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
