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
- `--mode lite|balanced|ultra` — how many tools to advertise (see
  [Tool surfaces](#tool-surfaces)).
- `--enable-act` (off by default) / `--awake` — opt into audited `act` (see
  [Audited act](#audited-act-v201-opt-in)).
- `--enable-hermes-review --reviewer <id>` (repeatable) — enable review channel.
- `--enable-relay --relay-peer <id>` (repeatable) — enable cross-host relay.
- `--liaison-db <path>` — mailbox path (default `$HERMES_HOME/liaison.db`).
- `--max-frame-bytes` (default `1048576`), `--seen-max-rows` (default `10000`),
  `--seen-max-age-days` (default `30`).

Run `conscio-mcp` directly (the console entry point), not `python -m
conscio.mcp.server`.

### Host-specific placement

The block above is the whole configuration; only the file it goes in changes.

| Host | File | Notes |
|---|---|---|
| Claude Code | `.mcp.json` in the project, or the plugin's bundled one | Installing the plugin writes it for you. |
| Verdent | `~/.verdent/mcp.json` (global, hot-reloaded) or `<project>/.verdent/mcp.json` | The project file wins where both define `conscio`. |

Two things bite on any host that launches the server itself rather than through a
shell:

- **`command` must resolve.** A host does not necessarily inherit your `PATH`, so
  if `conscio-mcp` lives in a virtualenv, give the absolute path to the binary
  (`<venv>/bin/conscio-mcp`) rather than the bare name. `conscio init` writes
  that absolute path for you when it can find one; only a hand-written entry
  still needs the substitution. The same applies to `--storage`: pass an
  absolute directory. A path recorded by the wizard points at the virtualenv
  that was current when it ran — rebuild or move that venv and `conscio init
  --repair` re-resolves it.
- **Conscio must be ≥ 4.1 on a host that validates tool names.** Verdent requires
  `^[A-Za-z0-9_-]{1,128}$` and rejects the pre-4.1 `conscio.recall` spelling. It
  still connects and still reports success — it just serves zero tools, and only
  its own log says why. If tools are missing there, check the version before
  anything else — and check *the one the host launches*:

  ```bash
  PY=$(head -1 "$(command -v conscio-mcp)" | sed 's|^#!||')   # the shebang's interpreter
  (cd /tmp && "$PY" -c "import conscio; print(conscio.__version__)")
  ```

  The console script pins an interpreter in its shebang, and that interpreter has
  its own `site-packages`. An editable checkout under one Python and an older
  wheel under another coexist happily, in which case `import conscio` in your
  shell reports 4.1 while the server the host actually starts is still 4.0 and
  still serving dots. Upgrade the interpreter in the shebang, not the one on your
  `PATH`. Run the check from outside a Conscio checkout — from inside, the source
  tree shadows the installed package and the command reports the version you were
  hoping for rather than the one being served.
- **Editing the file does not restart a running server.** A host that hot-reloads
  the config still keeps the old process — and the old code in its memory — alive
  for an entry whose name did not change, so an upgrade appears to do nothing.
  Delete the `conscio` entry, let the host reconnect without it, then put it back:
  that is what kills the old process. This is true of every future upgrade, not
  just the one that introduced it.

`conscio init --host verdent` writes this file for you (it backs the old one up
and reads it back to confirm), but the reconnect above is still yours to do.

## Tool surfaces

Every tool schema the server advertises costs context before the first prompt —
about 3100 tokens at `ultra`. A small model drowns in 35 tools; a large one is
crippled by 10. Four nested surfaces size the list to the model:

| Surface | Tools served | Advertised schema |
|---|---|---|
| `lite` | 10 | ~570 tokens, descriptions flattened to ≤120 chars |
| `balanced` | 18 | ~1520 tokens |
| `high` | 22 | ~1900 tokens |
| `ultra` (default) | 35 | ~3100 tokens |

**`lite`** — `advisory`, `events`, `feed`, `health`, `intercept`, `mode`, `note`,
`recall`, `remember`, `state`.
**`balanced`** adds — `context_budget`, `council`, `decide`, `handoff`,
`kg_query`, `recall_observations`, `verify`, `wings_search`.
**`high`** adds — `squad_experts`, `squad_opositors` (two squad wrapper tools
that expose Expert and Opositor voices via parameterised `voices` arrays; see
[Squads](#squads) below). Each wrapper replaces what would be 4 individual
voice tools — a 75% token saving over a per-voice surface.
**`ultra`** adds the remaining 13 base tools documented below. With `act`, review
and relay all enabled, the maximum served is 42.

The sets nest, so raising the surface never removes a tool. Three properties
follow from how the filter is applied:

- **Filtering touches the base set only.** A tool enabled by flag — `act`,
  review, relay — is never filtered out by the surface.
- **An unadvertised tool is still callable.** `tools/list` shrinks; `tools/call`
  does not. A host that knows a tool's name can invoke it in any surface.
- **`conscio_mode` is present in every surface.** In `lite` it is the only way
  back out.

Precedence is `--mode` on the CLI, then the persisted choice
(`<storage>/mcp_mode`), then the default. `conscio_mode` reads the current
surface and switches it at runtime; the server announces `tools.listChanged` in
the initialize handshake and emits the notification after a switch. It persists
*before* switching, so a failed write leaves the served surface intact and emits
no notification for a switch that did not happen. A guessed argument key raises
`InvalidParams` rather than returning the current mode — a response that would be
indistinguishable from a successful switch.

The startup banner reports `surface=` separately from `mode=`, which already
means act-vs-propose-only.

## Tools — BASE

### `conscio_feed(event, session_tokens?)`

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

### `conscio_note(event)`

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

### `conscio_advisory()` (pure read)

Current cognitive state. No args. Returns mode, metabolic tier, active goals,
recent reflections.

### `conscio_recall(query, k?, categories?)` (pure read)

Retrieve relevant past context (FTS5 BM25 + RAG fusion).

```json
{"query": "debug cron Hermes", "k": 5, "categories": ["consciousness", "session"]}
```

### `conscio_observe(tool, input?, output?, project?, agent?)`

Record a raw tool call into the isolated `obs.db` (v3.8 DeepMiner).
Fire-and-forget: never raises, never blocks, costs **0 LLM tokens**. Returns
`{observation_id}`, or `-1` if the write failed — telemetry must never break
the caller.

Since **v3.9** capture is full-fidelity, not clipped: a field is stored whole up
to `MAX_FIELD_BYTES` (1 MiB), against the 1024 chars the v1 schema kept. Rows
migrated from v1 stay clipped — the detail they lost was never written. On
Claude Code this tool is rarely called by hand, because the installed hook
records every tool call already.

```json
{"tool": "edit_file", "input": "fix auth bug", "output": "done", "project": "/proj"}
```

### `conscio_recall_observations(query, k?, full?)` (pure read)

FTS5 full-text search over recorded observations — distinct from
`conscio_recall`, which searches the content store. `input`/`output` carry the
**snippet window** around the hit (elided with `…`), not the whole stored row:
measured on real transcripts, ~110 tokens per recall instead of ~350. Pass
`full: true` when the entire observation is the answer.

```json
{"query": "authentication", "k": 5, "full": false}
```

### `conscio_propose_action(intent)`

Audit an explicit action intent with the Skeptic. **Never executes.**
Returns `{verdict: "PASS"|"FAIL", reasons, risk_flags, confidence, proposal}`.

```json
{"intent": {"action": "delete", "target": "/tmp/old.log", "reason": "cleanup"}}
```

### `conscio_propose_plan(goal, tools)`

Actor generates ONE action toward `goal`, constrained to the declared `tools`
vocabulary. **Never executes; not free-form; not multi-step.**

```json
{
  "goal": "limpar logs antigos",
  "tools": [{"name": "shell", "description": "run shell command"}]
}
```

### `conscio_state()` (pure read)

ConsciousnessState snapshot. No args.

### `conscio_events(type?, category?, since?, limit?)` (pure read)

Recent events. All args optional.

```json
{"type": "error", "category": "trading", "limit": 20}
```

### `conscio_handoff()` (pure read)

Latest session handoff (markdown). No args.

### `conscio_structure()` (pure read)

Workspace structural graph (consent-gated; data, never code). Returns
`loaded=false` if not consented or not loaded.

### `conscio_structural_lookup(key)` (pure read)

Resolve a structural node / hyperedge / community id from the loaded graph to
its detail. `null` on miss.

```json
{"key": "node:src/lib/auth.py"}
```

### `conscio_cognitive_cycle()` (v2.8)

Run one explicit cognitive pass: reflect → synthesize → propose/act → learn →
self-improve. Returns a report of each stage. The `act` stage only runs when
the server has `--enable-act` on.

### `conscio_evaluate(task_description, output)` (v2.15)

5-axis self-evaluation scorecard (accuracy, completeness, clarity,
actionability, conciseness). Scores 1–5 per axis with justification.

## Tools — GATES (v3.0)

### `conscio_decide(title, context, status, alternatives?, deciders?)`

Create an Architecture Decision Record. `status` is one of `proposed`,
`accepted`, `deprecated`, `superseded`. Returns ADR dict with unique ID.

### `conscio_council(question, context?)`

Convene a 3-voice deterministic council (Arquiteto, Cético, Pragmatista).
Optional LLM Critic if Awake Mode is active. Returns votes + majority verdict.

### `conscio_loop_gate(verifiable?, budget_ok?, has_tools?)`

Gate an autonomous loop. Checks three conditions, returns `approved` bool
plus vetoed conditions list. Emits `gate:vetoed` when denied.

### `conscio_delivery_check()`

Pre-close delivery check. Scans for blockers, rationalization patterns,
stale proposals, and disk space. Runs automatically in `engine.close()`.

### `conscio_investigate(target, action_type?)`

Verify that `target` was read before acting. Queries EventBus for
`investigate:read` events matching the target (substring match).

## Tools — PIPELINES (v3.0)

### `conscio_acceptance_criteria(goal?, depth?, risk_domains?)`

Generate intent-driven acceptance criteria. Auto-detects risk domains
(security/data/integration/compliance) and depth (quick/full).

### `conscio_verify(criteria?, criteria_source?)`

Verify acceptance criteria against `verify:evidence` events in EventBus.
Use `criteria_source="acceptance"` to load from the last acceptance event.

### `conscio_continuous_loop(task?, pattern?, frequency?, …)`

Select and gate an autonomous loop pattern: `sequential`, `continuous_pr`,
`rfc_dag`, `infinite`. Word-boundary keyword matching. Includes loop_gate.

### `conscio_strategic_compact(phase?, context_tokens?, context_window?)`

Advise on strategic context compaction. Checks token pressure, workflow
phase, and milestone count. Returns `should_compact`, `urgency`, keep/drop lists.

### `conscio_ledger(action, rollout_id?, candidates?, …)`

Recursive decision ledger. `action` is `record`, `query`, or `promote`.
Promotion gates: `paper` → `dry_run` → `live`, gated by coherence marks.

## Tools — DIAGNOSTICS (v3.0)

### `conscio_context_budget(context_tokens?, context_window?, detail?)`

Audit context window consumption. Returns token pressure, per-source
breakdown, metabolic tier sizes, and optimization recommendations.

### `conscio_eval_harness(action, eval_id?, eval_type?, task?, criteria?, results?, k_values?)`

Formal evaluation framework. `define` creates an eval, `run` records results
and computes pass@k metrics, `report` aggregates across all evals.

### `conscio_rules_distill(action, source_types?, min_occurrences?, rule_text?, rule_id?)`

Extract cross-cutting principles. `scan` finds repeated patterns in
skills/events/decisions, `distill` creates a rule, `list` shows all rules.

## Tools — ACT (opt-in `--enable-act`)

Available only with `--enable-act` (and engine Awake):

- `conscio_act(intent, idempotency_key?)` — execute a pre-audited action
- `conscio_report_result(ledger_id, result)` — feedback the outcome
- `conscio_pending()` — list actions awaiting approval
- `conscio_approve(ledger_id)` — approve a pending action
- `conscio_reject(ledger_id, reason)` — reject a pending action

See [Audited act (v2.0.1)](#audited-act-v201-opt-in) for the full flow.

## Tools — REVIEW (opt-in `--enable-hermes-review`)

Cross-agent `hermes_review` channel. Requires `--reviewer <id>` allowlist.

- `conscio_reviews(limit?)` — inbox of pending review requests
- `conscio_review_approve(request_id, verdict)` — issue verdict PASS + comments
- `conscio_review_reject(request_id, verdict)` — issue verdict FAIL + comments
- `conscio_poll_reviews()` — long-poll inbox for new requests

## Tools — RELAY (opt-in `--enable-relay`)

General cross-agent messaging. Requires `--relay-peer <id>` allowlist.

- `conscio_relay_send(to, type, payload)` — send free-form message
- `conscio_relay_inbox(limit?)` — read inbox
- `conscio_relay_read(ids)` — mark messages read
- `conscio_relay_broadcast(type, payload)` — fan-out to all peers

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

## VALID_TYPES (33)

```
tool_call, reflection, trade, error, anomaly, decision, perception,
goal_created, goal_expired, evolution_proposed, system, consciousness,
session, coherence:dissonance, awake:changed, workspace:changed,
structure:changed, proposal:audited, host:event, act:result, reflection_gate,
adr:proposed, adr:accepted, council:convened, gate:vetoed,
pipeline:acceptance, pipeline:verified, pipeline:compact, pipeline:ledger,
diagnostic:budget, diagnostic:eval, diagnostic:rule
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

1. Host sends perception via `conscio_feed`.
2. Conscio reflects; the host reads `conscio://advisory`.
3. Host asks `conscio_propose_action` (or `propose_plan`) about an intent.
4. Conscio runs the Skeptic and returns a `PASS`/`FAIL` verdict with reasons.
5. **The host** decides and executes.
6. Host feeds the result back as a new Event.

Conscio signs and audits the intent; the host pulls the trigger.

## Audited act (v2.0.1, opt-in)

Off by default. Launch with `conscio-mcp --enable-act` and the engine **Awake**
(`--awake`, or a persisted awake state). Only then do five more tools appear:
`conscio_act`, `conscio_report_result`, `conscio_pending`, `conscio_approve`,
`conscio_reject`.

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

1. Host calls `conscio_act(intent)` with a concrete `{tool, args, rationale,
   expected_outcome}` (optionally an `idempotency_key`). Conscio runs the Skeptic.
2. Skeptic FAIL → `{status: "rejected"}`. PASS + `low`/`medium` + `auto` →
   `{status: "executable", packet, ledger_id}` (claimed). PASS + `high` /
   `require_approval` / `hermes_review` → `{status: "pending_approval"}`. Engine
   asleep or breaker lockdown → `{status: "gated"}`.
3. For a pending action, a human/Hermes calls `conscio_approve(ledger_id)`
   (→ an executable packet) or `conscio_reject(ledger_id, reason)`.
4. **The host executes the packet** — Conscio never does.
5. Host calls `conscio_report_result(ledger_id, result)`; Conscio closes the
   ledger entry, emits an `act:result` event, and feeds the breaker/trust. A
   duplicate report returns `already_reported`.

Every action writes an `ActionLedger` row — the same audited trail native `act()`
uses. `act` is **never** local dispatch: Conscio audits, the host executes.

## Squads (v4.4)

Two orthogonal advisory squads run alongside the Council. Each squad is a
closed namespace — its voices, event types, and MCP tool are isolated from
the Council and from each other.

### `conscio_squad_experts(question, context, voices?, use_llm?)`

Convene the **Experts** squad — constructive, technical specialisation.

| Voice | Focus | Deterministic | LLM opt-in |
|---|---|---|---|
| `optimizer` | Performance: hot paths, latencies, query plans, algorithmic complexity | ✅ | ✅ |
| `auditor` | Security: static analysis, secrets, permissions, threat modelling | ✅ | ✅ |
| `qa` | Quality: tests, fuzzing, edge cases, regression, coverage gaps | ✅ | ✅ |
| `promptor` | Prompt optimisation: intent extraction, clarity, completeness, mode selection | ✅ (always) | — |

Parameters:
- `voices` — array of voice names (default: all available in current mode).
  Unknown names are ignored silently.
- `use_llm` — boolean (default `false`). When true and an adapter is attached,
  voices that support `analyze_llm()` will use it; deterministic fallback
  runs for the rest.
- `question` (required) — the decision or artefact to evaluate.
- `context` — additional context string.

Returns `{question, squad: "experts", voices: [...], recommendation, votes_summary}`.

Event emitted: `squad:experts:convened`.

### `conscio_squad_opositors(question, context, voices?, use_llm?)`

Convene the **Opositors** squad — hostile pressure to validate premises.

| Voice | Focus | Deterministic | LLM opt-in |
|---|---|---|---|
| `caustic` | Acidic visual/UX critique; exposes hypocrisy in design decisions | ✅ | ✅ |
| `devils_advocate` | Argues the opposite position; forces proof-by-contradiction | ✅ | ✅ |
| `skeptic_engineer` | Hunts over-engineering, unnecessary complexity, ego-driven architecture | ✅ | ✅ |
| `douche_reviewer` | Passive-aggressive code review; sniffs slop, regex hacks, iframe abuse | ✅ | ✅ |

Parameters and return shape match `squad_experts`. Event emitted:
`squad:opositors:convened`.

> **Caustic tone boundary.** Permitted: sarcasm, irony, crude technical
> metaphors. Blocked: personal attacks, hate speech, slurs, attacks on
> protected categories. The deterministic path generates this boundary by
> construction; the LLM path is guided by a system prompt that enforces it.

### Voice availability by surface mode

| Surface | Council | Experts | Opositors |
|---|---|---|---|
| `lite` | 4 voices (det) | — | — |
| `balanced` | 4 voices (det) | optimizer, auditor (det) | — |
| `high` | 4 voices (det) | all 4 (det) | caustic, douche_reviewer (det) |
| `ultra` | 4 voices (det+LLM critic) | all 4 (LLM opt-in) | all 4 (LLM opt-in) |

> The Council is unaffected by the squad system — `engine.council()` and
> `conscio_council` remain the same API with the same 4 voices.

## Common pitfalls

1. **Invalid `type` / `category`** raises `ValueError` before any DB write. Check
   the valid sets above.
2. **`conscio_note` does NOT run `reflect`** — use `conscio_feed` if you want the
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
10. **`conscio_cognitive_cycle` is explicit** — the daemon's autonomous loop runs
    the same stages, but calling this tool runs **one** pass and returns a report.
    It is not a long-running background loop.
