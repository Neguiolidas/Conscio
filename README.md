# Conscio 🧠✨

**A self-awareness framework for AI agents** — context-aware memory,
introspection, goal generation, and an audited agency layer that lets a model
act on its own conclusions under hard safety gates.

> *"The first step toward consciousness is knowing what you are and what limits you."*

Conscio runs **local-first** and **zero-deps at the core** (`numpy` + `sqlite3`,
nothing else). It is designed to make small, local models punch far above their
size by giving them memory, self-judgment, and procedural skill — and to prove
that claim by measurement, not assertion.

- **Current release:** `v2.11.0` — "Reach" (one global install, per-host minds, native Claude Code integration): install Conscio **once** and bind **many agentic hosts** (Claude Code, Antigravity, any MCP host) on one machine, each to its own **space** (`~/.conscio/instances/<slug>/` — its own identity, memory, ledger) while sharing one **society** (`~/.hermes/{noosphere,liaison}.db`). A new interactive **`conscio init`** wizard (pure stdlib) creates the space, emits + **read-back-verifies** the host's MCP launch config (`--storage` + per-host `CONSCIO_VAULT_DIR` vault), enables extras (**Graphify**), and can start **Awake** (`start_new_session` so it survives logout; PID liveness matches the real daemon cmdline). For Claude Code it materializes a **native bundle** into `~/.claude/`: **10 `/conscio:*` slash commands**, a `conscio` **skill** (so the agent reaches for `conscio.*` MCP tools proactively — recall at start, remember on a settled decision), and a **defensive** SessionStart awareness hook (always exits 0). Per-host secret **vault** (logical least-privilege; legacy global vault stays the backward-compat default). **R6** startup validation warns on a missing/blank binding (`conscio init --repair`). Debt-zero on the cognitive core (`engine`/`liaison`/`noosphere`/`mcp` protocol byte-identical). `pip install conscio` then `conscio init`.
- **Prior:** `v2.10.0` — "Initiative" (Relay Phase 3, proactive cognition): an Awake agent can now **speak first**. `conscio-daemon --initiate` lets the daemon proactively **open** directed conversations with peers — and `--initiate-broadcast` announce to all — with the opener generated through the agent's own **read-only** cognition (identity + recalled memory + advisory). It only speaks when it genuinely has something to say (the mind replies `NOTHING` to stay silent), behind layered safety gates: requires **Awake Mode** (re-checked every cycle, not just at startup); a cheap `advisory()` precondition gates the expensive adapter call; a cadence cap (`--initiate-interval`, default 300s); directed **no-storm** (never re-opens while awaiting a reply); broadcast **outstanding-guard** (never re-broadcasts with zero peer engagement); and `action_lockdown`/brake **fail-closed** suppression. Proactivity is **read-only** — the peer-facing text never enters episodic memory, the world-model, or goals (spy-engine + AST proven). Also fixes the v2.7.0 `responder` to honor a runtime sleep. Debt-zero, daemon-perceives / server-acts intact. `pip install conscio`.
- **Earlier:** `v2.9.1` — "Memory in the loop" (Relay Phase 3, slice 3): the cognition-routed responder can now **remember**. With `conscio-daemon --cognize-remember` (rides on `--cognize`, OFF by default), each relay exchange is written to **episodic memory** (`content_store`, category `external`) so future cognition can `recall` it. The integrity boundary stays narrow: the write goes **only** through `content_store.index` — `perceive`/`reflect`/`run` are never called, so a peer's words become recall-able memory but never drive the world-model or goals (proven by a spy-engine test); with remembering off, `cognize_respond` is byte-equivalent to v2.9.0. Also **fixes** the adapter Hub-vault key resolution — `build_adapter_from_config` now falls back to the `~/.config/conscio/keys/` vault instead of building a keyless adapter (HTTP 401 since v2.7.1). Debt-zero, daemon-perceives / server-acts intact. `pip install conscio`.
- **Earlier:** `v2.9.0` — "Mind in the loop" (Relay Phase 3, slice 2): a relay auto-reply can now come from the agent's **actual cognition** instead of a stateless adapter fork. A new engine-side `conscio/agency/relay_cognize.py` builds the prompt from three **read-only** engine surfaces — identity (`get_state_for_injection`), recalled memory (`recall`, with the peer's text as the *query* only), and the advisory signal (`advisory`) — plus the v2.8.2 multi-turn transcript, then a raw adapter call. `conscio-daemon --cognize` (rides on `--auto-respond`) selects this path. The **integrity boundary** is load-bearing: peer text NEVER enters episodic memory / the world-model / goals — the module calls only the engine read-trio, proven by a spy-engine test (mutators raise) and an import-shape test (no `conscio.engine` import). Replies are capped at `max_reply_chars` (2000) to damp reflexive long replies; the loop-breaker is unchanged. Off by default, allowlist-gated, debt-zero (`relay_respond.py` and the liaison layer byte-identical), daemon-perceives / server-acts intact. `pip install conscio`.
- **Earlier:** `v2.8.2` — "Conversation" (Relay Phase 3, slice 1): the cross-agent relay becomes a **conversation**. A new pure `mailbox.thread(a, b)` returns the last-N messages between two instances (both directions, chronological), and `conscio-daemon --auto-respond` now prompts the adapter with that **multi-turn transcript** (a `peer:`/`me:` thread, review rows excluded) instead of a single inbound message — char-budget clamped, the reader staying pure. A new `conscio.relay_broadcast` MCP tool (under `--enable-relay`) **fans a message out to every `--relay-peer`** (best-effort per peer). Engine-free, allowlist-gated, no schema change; the daemon-perceives / server-acts boundary holds — broadcast is a mailbox write, never `host_act`. Debt-zero. `pip install conscio`.
- **Earlier:** `v2.8.1` — "Reins" (slice 2): the dropped Hub awake control, **redone safely**. `conscio-hub --enable-daemon-control` exposes `PUT /api/daemon/awake` + `GET /api/daemon/control`, writing `daemon_control.json` to the storage dir (atomic, via a new pure `conscio/hub/control.py`); a daemon run with `conscio-daemon --watch-control` reads that file at the top of each cycle and applies it via `engine.wake()`/`engine.sleep()`. **No signals, no PID, no `os.kill`** — the v2.7.1 bug where the toggle killed the daemon is now structurally impossible. **Double opt-in**, both flags off by default (flag off → routes 404, daemon branch skipped, byte-identical). Also closes two audit gaps: the install docs now list **all six** console scripts, and CI tests the **full claimed 3.10–3.13** range. Debt-zero. `pip install conscio`.
- **Earlier:** `v2.8.0` — "Reins" (slice 1): the **Observatory** gains three read-only panels — **Daemon** (`GET /api/daemon`, the daemon's last atomic `daemon_heartbeat.json`), **Relay** (`GET /api/relay/inbox`, this instance's liaison inbox via a new `LiaisonProjection` that opens `liaison.db` with `mode=ro`, full payload, never marks read — it cannot reuse the read-write `mailbox.inbox()`), and **Identity** (`GET /api/identity`, reads `instance.json` read-only, never creating one). Still **GET-only** (mutation → 405), loopback-only, **engine-free**, with **no `--enable` flag** — launching the viewer is the opt-in. `pip install conscio`.
- **Earlier:** `v2.7.1` — "Hub: key vault + provider fix": the Hub's Active Brain page gains an **API Key** field. On Save the raw key is written to a per-provider file under `~/.config/conscio/keys/` (created **0600**, dir **0700**) and the config keeps only an `api_key_env` reference — `validate()` still rejects a raw `api_key` in `config.json` and the key is never echoed over the API. The provider select no longer reverts on poll (`resolve_provider` merges the active adapter's `base_url`/`api_key_env`; the form seeds only on first load), and **Test** now shows latency + a sample + detected models. Vault filenames are validated against the env-name regex before any FS op (no path traversal). `pip install conscio`.
- **Earlier:** `v2.7.0` — "Phase 2: General Relay Auto-Respond": an **Awake** daemon can **auto-reply** to unread free-form relay messages from trusted peers — generating one reply each via its LLM adapter and sending it back, no human in the loop. Behind `--auto-respond` (OFF by default; inert without the `relay` sensor + an adapter + `--awake` + `--relay-peer`), with `--respond-limit` (default 10) capping adapter calls per cycle. The loop is **1-turn bounded**: a peer's auto-reply is consumed but never re-answered, so two auto-responders cannot ping-pong. The pure responder `conscio/agency/relay_respond.py` is **engine-free** and the MCP server is **untouched**. `pip install conscio`.
- **Earlier:** `v2.6.3` — "Ressalvas": post-ship hardening of the Awake Relay loop. `--auto-review` no longer opens a liaison `SELECT` per tool call — the poll is **throttled to once per 5 s** per server (the `host_act` gate stays the authority). The `RelaySensor` id prefix in observations widens 8 → 12 chars to avoid visual collision, and an **end-to-end integration test** now wires a real mailbox + `HostActChannel` + daemon sensor + server auto-apply, proving the perceive → verdict → approve loop in-suite. `pip install conscio`.
- **Earlier:** `v2.6.1` — "Relay": **general cross-agent messaging** over the same Liaison mailbox. Behind `--enable-relay` (independent of act / hermes-review), `conscio.relay_send`/`relay_inbox`/`relay_read` carry **free-form directed messages** between agents — Claude ↔ Hermes ↔ Antigravity ↔ OpenClaw. `--relay-peer` gates **both** send and receive; the two review types stay **reserved** (the channels never bleed); payloads are capped at 64 KB; read messages are purged after 7 days (unread never deleted). A **dumb pipe** — engine-free, never touches an act; no crypto (shared-filesystem trust domain). Debt-zero: no DB schema change, the review path byte-identical. `pip install conscio`.
- **Earlier:** `v2.6.0` — "Liaison": cross-agent `hermes_review`. A `hermes_review`-gated act proposed by one agent can be **approved or rejected by a different agent** over a file-mediated shared mailbox (`$HERMES_HOME/liaison.db`, separate from `noosphere.db`). New **engine-free** `conscio/liaison/` (mailbox + pure review protocol); MCP tools behind `--enable-hermes-review` — `conscio.reviews`/`review_approve`/`review_reject` (reviewer role) and `conscio.poll_reviews` (proposer role). A verdict is applied **only** from an allowlisted `--reviewer` and **only** through the unchanged local `host_act` gate; `fp` binds each verdict to one exact proposal (proposer id + ledger id), so cross-instance confusion and replay are impossible. No crypto (shared-filesystem trust domain); directed-only; off by default. `pip install conscio`.
- **Earlier:** `v2.5.0` — "Society view": the Observatory now also projects the **host-shared noosphere** — the "society" of instances that have published into `noosphere.db`. `conscio-observatory` gains read-only `/api/society/{members,skills,records}` (the census plus published skills/records, **metadata only** — artifact/bundle BLOBs omitted) and Society tabs in the viewer, over the same **engine-free** projection that opens `noosphere.db` with `mode=ro` (no `PRAGMA`, `SELECT` only). `mode=ro` reads the latest committed WAL rows even under a concurrent peer writer (`immutable=1` deliberately rejected). GET-only (mutation → 405); **no `--enable` flag** — launching it is the opt-in. `pip install conscio`.
- **Earlier:** `v2.4.0` — "Observatory": a **read-only** window into one instance's mind. `conscio-observatory --storage DIR [--token TOK]` serves a **loopback-only** HTTP viewer of the persisted logs, goals, actions, skills, and state — over an **engine-free** projection that opens `conscio.db` with `mode=ro` (no `PRAGMA`, `SELECT` only) and parses `goals.json`/`state_summary.json`. It serves **GET only** (every mutation verb → 405) and reads even a **cold** instance with no engine running. The MCP server also gains read-only `conscio.state`/`conscio.events`/`conscio.handoff` **tools** that delegate to the same helpers its resources use. **No `--enable-observatory` flag** — it has no write/execute surface, so launching it is the opt-in. `pip install conscio`.
- **Earlier:** `v2.3.0` — "Promotion": a quarantined skill that has earned **≥ 3 clean local trials** can now be **graduated into the live skill library**. `conscio promote --quarantine ROWID --enable-promote` re-checks the content hash, verifies every tool in the plan exists locally (**tool-existence check**), and grafts the skill seeded with the counters it earned in the sandbox — **never the origin's stats**, so no trust is inherited. Engine-side write; the noosphere stays engine-free and read-only on `conscio.db`. Never overwrites a local skill; idempotent; off by default and independent of `--enable-trial`/`--enable-act`.
- **Earlier:** `v2.2.2` — "Trial / execution path": a quarantined imported skill can **prove itself locally** before promotion — `conscio trial --quarantine ROWID --enable-trial` replays the fixed foreign plan in a **throwaway, fs-only sandbox** through the full safety stack (`validate → precheck → HIGH-block → Skeptic → dispatch`), recording a binary pass/fail on the quarantine row. Fully **isolated** — never writes the live agent's ledger/skills/trust/breaker; tamper refuses without counting. Off by default; independent of `--enable-act`.
- **Earlier:** `v2.2.1` — "Mutual audit": an instance publishes a non-sensitive projection of its action ledger (`conscio noosphere publish-record`) to the host-shared `noosphere.db`, and a peer **independently audits** it (`conscio noosphere audit`) — deterministic, read-only, engine-free. The auditor re-derives track-record, breaker quarantines, and a foreign-trust level under its **own** thresholds (parity-tested against the engine) and runs a discipline check (did the peer execute actions its own Skeptic FAILed?). No inherited trust; report-only; the auditor persists nothing.
- **Earlier:** `v2.2.0` — "Society" (Noosphere Core): same-host Conscio instances **share locally-proven skills as data**. `conscio noosphere publish` copies your proven skills (stats stripped) into a host-shared `noosphere.db`; `conscio noosphere import` pulls another instance's skills into a local **quarantine** after execution-free static revalidation. Engine-free; opens your live `conscio.db` **read-only**; zero network/socket. Nothing imported is trusted, served, executed, or promoted — trust is never inherited.
- **Earlier:** `v2.1.0` — "Hub": a **localhost stdlib HTTP control plane** (`conscio-hub`) to swap the active model/provider and register custom OpenAI-compatible providers without hand-editing JSON. Engine-free; config applies on next boot. Per-provider model auto-discovery; one-shot smoke test before save. `api_key_env` resolution (env var name → value at adapter build time) is now additive to raw `api_key` — daemon + MCP inherit it. Hub never returns a raw API key.
- **Earlier:** `v2.0.1` — "Connect" continued: **opt-in, host-executed audited `act` over MCP**. Conscio audits + gates + ledgers an action and returns an *execution packet*; the **host** executes and reports the outcome back — Conscio still never touches the world. Off by default (`conscio-mcp --enable-act`, requires the engine **Awake**); the host declares its tool manifest (`name`/`params`/`risk`/`approval_policy`) in `initialize`; HIGH-risk / `require_approval` actions stay **queued for human/Hermes approval** (`conscio.pending` → `conscio.approve`). Also: `conscio-mcp` adapter parity (six providers from config) and the **R-05** content-store dedup fix — shipping **debt-zero**. Cognition (`reflect()`) untouched; purely additive.
- **Earlier:** `v2.0.0` — "Connect", the **Embodiment** phase: Conscio becomes embeddable in **any** MCP host (CLI, IDE, agent) via a hand-rolled **stdlib-only** MCP stdio server (`conscio-mcp`, newline-delimited JSON-RPC 2.0). Zero new runtime dependency; nothing opens a socket. The v2.0.0 surface was **propose-only** — perceive, reflect, recall, and **audit**, but never execute. Cognition (`reflect()`) untouched; the public API unchanged (MCP purely additive).

---

## What Conscio does

- **Knows itself** — detects its model and context window (offline & deterministic
  by default; opt-in auto-detection from a JSON config, an OpenAI-compatible
  endpoint, LM Studio, or GGUF), adapts its footprint.
- **Reflects continuously** — a passive inner-monologue loop that observes,
  assesses confidence, and summarizes (`engine.reflect()` — advisory, never acts).
- **Generates its own goals** — driven by curiosity, maintenance, and evolution.
- **Acts under audit** — an opt-in agency layer (`engine.act()`) that proposes,
  audits, risk-gates, and only then executes — with a human gate for anything risky.
- **Learns procedures** — successful audited plans become reusable skills
  (procedural memory), fed back to the actor as few-shot exemplars.
- **Judges its own quality** — confidence calibration, blind-spot detection,
  coherence/dissonance metrics, meta-reflection.
- **Stores & retrieves knowledge** — FTS5 BM25 dual-index with RRF merging;
  optional semantic recall.
- **Consolidates while idle** — a dream cycle that releases, prunes, reconciles,
  crystallizes, and distills.
- **Persists across sessions** — heartbeat/handoff continuity with on-demand injection.
- **Knows its codebase (structurally)** — optional, consent-gated ingestion of a
  Graphify graph, distilled to a compact signal injected budget-aware; tracks
  structural drift + staleness vs the repo HEAD. Data, never code (R10).
- **Plugs into any host (v2.0)** — a stdlib-only MCP stdio server (`conscio-mcp`)
  lets any CLI/IDE/agent feed it perception and consume its cognition + audited
  proposals live. Propose-only: it signs and audits intent; the host executes.

`reflect()` is the **passive heart** and is never allowed to act. Everything that
can change the world lives behind `act()` and its safety gates. This separation
is non-negotiable (see [Safety Rules](#safety-rules-non-negotiable)).

---

## Context-aware modes

Conscio detects the model's context window and adapts how much "consciousness
state" it injects. The mode governs **injection budget only** — never whether
the framework runs.

| Mode | Context window | Injection budget | What's injected |
|---|---|---|---|
| **Minimal** | < 128k | ≤ 200 tokens | Off-context everything; on-demand retrieval |
| **Compact** | 128k–256k | ≤ 500 tokens | Summary + last reflection + top goals |
| **Standard** ⭐ | 256k+ | ≤ 1000 tokens | Full state; world subgraph; self-assessment |

⭐ **Standard (256k+) is the recommended operating class.** Conscio runs on
anything from **8k context up** — small windows simply get the Minimal budget.

---

## Architecture (v2.0.1)

```
┌──────────────────────────────────────────────────────────────────────┐
│                        ConsciousnessEngine                            │
│                  orchestrator · lifecycle · injection                 │
└──────────────────────────────────────────────────────────────────────┘
   │
   │  reflect()  ── passive, advisory, append-only ──────────────────────┐
   ▼                                                                      │
┌─────────────── Witness loop (v0.1) ───────────────────────────────────┐│
│ InnerMonologue · WorldModel · MetaCognition · GoalGenerator           ││
│ AutoEvolution · ContextManager · ModelRegistry                        ││
└────────────────────────────────────────────────────────────────────────┘│
┌─────────────── Substrate (v0.2) ──────────────────────────────────────┐ │
│ ContentStore (FTS5 BM25 + RRF) · EventBus (SHA-256 dedup)             │ │
│ FilterPipeline (sanitize/redact) · TokenTracker · Migrator            │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Continuity (v0.2.3) ───────────────────────────────────┐ │
│ SessionLifecycle (6-step handoff) · SessionRAG (optional, lazy)        │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Metabolism & self-judgment (v0.3–0.5) ─────────────────┐ │
│ MetabolicContext (VITAL/ACTIVE/FATIGUE/CRITICAL) · DreamCycle         │ │
│ entropy pruning · friction · meta-reflect · ShardEngine · layering    │ │
└────────────────────────────────────────────────────────────────────────┘ │
┌─────────────── Coherence (v0.6–0.8) ──────────────────────────────────┐ │
│ CoherenceEngine (epistemic/reality/ontological/temporal)             │ │
│ semantic reconciliation (antonym axes) · voice & axis presets         │ │
└────────────────────────────────────────────────────────────────────────┘ │
                                                                            │
   act()  ── opt-in agency, audited, gated ◀────────────────────────────────┘
   ▼
┌─────────────── Agency · conscio/agency/ (v1.0–1.1, F1–F4) ────────────┐
│ InferenceAdapter (Mock/Ollama/llama.cpp/OpenAI-compat) · OutputGateway │
│ ToolRegistry (sandboxed, no network) · ActPipeline · ActionLedger      │
│ Skeptic (hostile audit) · TrustMatrix · CircuitBreaker (quarantine)    │
│ ProbeSuite/ModelProfile · GBNF compiler · GoalArbiter · AutonomyLoop   │
│ Meter/MeteredAdapter · SkillLibrary (procedural memory) · Bench        │
└────────────────────────────────────────────────────────────────────────┘
┌─────────────── Structural cognition (v1.6–1.8) ───────────────────────┐
│ GoalOrigin provenance gate · advisory() consumption pull              │
│ StructuralDistiller (graph.json → ranked signal; data, never code/R10) │
│ budget-adaptive injection · consent (per-workspace, switch-safe)       │
│ drift + freshness (vs repo HEAD, pure .git read; no subprocess)        │
└────────────────────────────────────────────────────────────────────────┘
┌─────────────── Embodiment · conscio/mcp/ (v2.0, propose-only) ─────────┐
│ conscio-mcp: hand-rolled JSON-RPC 2.0 over stdio (stdlib only)         │
│ bounded-at-source frame reader · version negotiation · structured errs │
│ tools: feed/note/advisory/recall/propose_action/propose_plan          │
│ resources: advisory/state/events/handoff · idempotent (mcp_seen.db)    │
│ NEVER executes — host stays sovereign; act → v2.0.1                    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Quick start

```python
from conscio import ConsciousnessEngine

# Passive consciousness — auto-detects model and mode
with ConsciousnessEngine(model_name="kimi-k2.6") as engine:
    result = engine.reflect(
        world_state="All systems operational",
        confidence=0.8,
        anomalies=["Unusual latency spike detected"],
    )

    # Compact state for context injection
    injection = engine.get_state_for_injection()

    # Query / update the world model
    engine.world.add_entity("server", "system", state="healthy")
    engine.world.query("server health")

    # Cross-session memory (ContentStore FTS5 + optional SessionRAG)
    hits = engine.recall("latency incidents")
```

### Opt-in agency (audited, propose-only by default)

```python
from conscio.agency import OllamaAdapter

engine.attach_adapter(OllamaAdapter(model="qwen3.5:0.8b"))
# Local (Ollama/llama.cpp/LM Studio/OpenAI-compatible) or a frontier API.
# These call the SAME model APIs that power Claude Code / Antigravity, so Conscio
# can think with those models — they do NOT make Conscio run *inside* those tools.
# To run *inside* a host, use the v2.0 MCP server (`conscio-mcp`, see Embodiment):
#   from conscio.agency import AnthropicAdapter, GeminiAdapter
#   engine.attach_adapter(AnthropicAdapter(model="claude-sonnet-4-6"))  # ANTHROPIC_API_KEY
#   engine.attach_adapter(GeminiAdapter(model="gemini-2.5-pro"))        # GOOGLE_API_KEY

report = engine.act()                 # downstream of reflect(); proposes only (L1)
if report.status.value == "proposed":
    print(report.proposal.tool, report.proposal.args)
    engine.approve(report.ledger_id)  # the human gate executes it

# Capability-aware autonomy loop under a binding budget
engine.probe()                        # lazy, empirical capability measurement
engine.run(budget=...)                # L3 heartbeat: reflect → act → dream, gated
```

Autonomy is **earned and measured**, never assumed: `ProbeSuite` measures the
attached model, `TrustMatrix` grants L1/L2/L3 from real calibration and ledger
history, and the `CircuitBreaker` quarantines misbehaving goals. HIGH-risk
actions are *always* queued for a human (R6).

---

## Safety rules (non-negotiable)

1. **No autonomous self-modification** — evolution proposals require human approval.
2. **Context injection has hard limits** — never exceeds the mode budget.
3. **Goals never execute directly** — only through the audited `act()` pipeline:
   validated output contract + semantic audit (Skeptic) + risk gating + earned
   autonomy (TrustMatrix) + circuit breaker with per-goal quarantine and lockdown.
4. **Reflections are append-only** — never edited once written.
5. **Cannot modify its own safety rules** — no self-referential gate bypass.
6. **HIGH-risk actions always require human approval** — never auto-executed.
7. **No network in the tool registry** — the only network the core may touch is
   the InferenceAdapter (localhost by default); shell lives in the sibling
   `conscio-shell`, outside this repo.
8. **Every external effect goes through the ActionLedger** — append-only, auditable.
9. **Autonomous operation requires Awake Mode (R9)** — the self-initiated
   heartbeat (`engine.run()` and the daemon) only acts when the persisted `awake`
   flag is on; **default OFF**. Asleep, it perceives and `reflect()`s only — zero
   arbiter/act/dream. A human's direct `engine.act()` is not gated by R9.

---

## Live mode — daemon, sensors & Awake Mode (v1.5)

Conscio can run as a **living process** that perceives the world each cycle and
acts **only when explicitly awake** (R9, default OFF):

```python
from conscio import ConsciousnessEngine, HostSensor
from conscio.daemon import Daemon

engine = ConsciousnessEngine("glm-5.1", storage_path="~/.conscio/live")
engine.wake()                              # R9: opt in to autonomy (persisted)
Daemon(engine, sensors=[HostSensor()], interval=30).run()   # perceive→reflect→act
```

- **Awake Mode** — `engine.wake()` / `engine.sleep()` (or `conscio awake|sleep`);
  asleep = advisory reflect-only, awake = full loop. The flag persists and emits
  an auditable `awake:changed` event.
- **Reference sensors** — `HostSensor` (read-only host facts) and `AgentSensor`
  (read another agent's session state), both `Risk.LOW`; ship as `conscio.sensors`
  entry points (`conscio plugins` lists them). Write your own `SensorAdapter`.
- **Daemon** — `conscio-daemon --sensors host --interval 30` (add `--awake` to
  enable autonomy; `--once` for a single cycle). Guarded sensors, graceful
  `SIGTERM`, pidfile, resume-from-state on restart.
- **Workspace awareness** — `WorkspaceContext` detects the active workspace root
  and environment class (IDE/CLI vs workspace-switching agents) and signals
  `workspace:changed`.

---

## Structural cognition (v1.6–1.8)

Conscio can give the refined model **structural awareness of the codebase it
works in**, distilled from a [Graphify](https://github.com)-format `graph.json` —
consumed as **data, never code** (R10: no `networkx`, no Graphify runtime
dependency, every field inert).

```python
# Consent is per-workspace and defaults OFF — nothing is read until granted.
#   conscio consent project        # ingest THIS workspace's graphify-out/graph.json
sig = engine.load_structure("graphify-out/graph.json",
                            workspace_id=ws.id, root=ws.root)

engine.get_state_for_injection()   # appends a budget-adaptive structure block (labels only)
engine.structural_lookup("conscio_engine_reflect")   # on-demand drill-down
engine.structural_delta()          # what changed since the last load (v1.8)
engine.structural_freshness()      # is the graph behind the repo HEAD? (v1.8)
conscio structure                  # read-only drift + freshness report (CLI)
```

- **Distiller** — thousands of nodes → ~24 curated hyperedges + per-community
  digests; a pure `lookup()` resolves any node/hyperedge/community id on demand.
- **Budget-adaptive injection** — sized to the model's context window
  (~120→1200 tokens), **additive** (the consciousness-state block is byte-for-byte
  unchanged), **labels only** — never raw node-ids.
- **Consent-gated & switch-safe** — per-`Workspace.id`, default OFF; a
  workspace-switching agent only ingests a consented workspace, and unloads on
  switch-away — one project's structure never leaks into another.
- **Drift & freshness (v1.8)** — a per-workspace baseline lets the agent notice
  when the graph was rebuilt (commit moved, communities/hyperedges
  added·removed·resized) or has gone stale vs the repo `HEAD` (read **purely from
  `.git`** — no `git` subprocess). Surfaced in `advisory()` + the daemon
  heartbeat; a passive `structure:changed` event fires on real drift.

See [the integration guide](docs/guides/integration.md#structural-cognition).

---

## Embodiment — MCP server (v2.0)

Conscio ships a hand-rolled, **stdlib-only** [MCP](https://modelcontextprotocol.io)
stdio server (newline-delimited JSON-RPC 2.0) so **any** MCP host — a CLI, an IDE,
or an agent — can plug into a Conscio instance and consume its cognition as a live
consciousness-layer. Zero new runtime dependency; nothing opens a socket.

```jsonc
// point any MCP host at the console entry point (one engine = one workspace)
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

In v2.0.0 the surface is **propose-only**: Conscio perceives (`feed`/`note`),
reflects, recalls, and **audits** proposed actions (`propose_action` /
`propose_plan` → Skeptic verdict), but never executes — the host stays sovereign
over execution. `feed`/`note` are **idempotent** on `event.id` (a duplicate
returns the exact prior result, so retries never inflate the world model). The
transport is hardened against hostile host input (malformed/oversized/partial
frames, wrong protocol version, pre-initialize requests) by a seeded stdlib fuzz
battery. Audited execution over MCP (`act`) lands in v2.0.1 with a host-execution
callback model.

> *Conscio signs and audits the intent; the host pulls the trigger.*

See [the MCP guide](docs/guides/mcp.md).

---

## Module reference

**Core / Witness (v0.1)** — `ConsciousnessEngine`, `ContextManager`,
`ModelRegistry` (`conscio/models.py`), `WorldModel`, `MetaCognition`,
`GoalGenerator`, `AutoEvolution`, `InnerMonologue`.

**Substrate (v0.2)** — `ContentStore` (FTS5 BM25 dual-index, RRF, 8 categories),
`EventBus` (SHA-256 dedup, priorities, expiration), `FilterPipeline`
(`conscio/output_filter.py` — StripAnsi/CollapseBlank/MaxLines/TruncateLines +
`DedupBlocks`/`SecretMask`), `TokenTracker`, `Migrator`.

**Continuity (v0.2.3)** — `SessionLifecycle` (extract → enrich → emit → index →
reflect → write; heartbeat <1.5KB + handoff), `SessionRAG` (optional, lazy,
Ollama `nomic-embed-text`, numpy cosine; graceful FTS5 fallback).

**Metabolism & self-judgment (v0.3–0.5)** — `MetabolicContext` (life-energy
tiers, advisory), `DreamCycle` (Release → Prune → Reconcile → Crystallize →
Distill), entropy pruning, friction, meta-reflect, `ShardEngine` (cognitive-mode
inference), content layering, trajectory vector.

**Coherence (v0.6–0.8)** — `CoherenceEngine` (recursive-coherence metric;
advisory `coherence:dissonance` event), semantic reconciliation via antonym axes
(`conscio/semantic.py`, packs in `conscio/presets/axes/`), self-prompting, voice
presets.

**Agency — `conscio/agency/` (v1.0–1.1)**

- *F1 "Spine"* — `InferenceAdapter` (Mock/Ollama/LM Studio/llama.cpp/OpenAI-compat,
  stdlib urllib), `OutputGateway` (tiered decoding), `ToolRegistry` (sandboxed,
  risk levels, no network), `ActPipeline`/`act()` (L1 PROPOSE), `ActionLedger`.
- *F2 "Immunity"* — `Skeptic` (hostile-auditor clean call; fail-closed),
  `TrustMatrix` (earned autonomy), `CircuitBreaker` (per-goal quarantine).
- *F3 "Volition"* — `ProbeSuite`/`ModelProfile` (5 empirical micro-probes,
  SQLite-cached, no hardcoded model table), embedded schema→GBNF compiler,
  `GoalArbiter` + `AutonomyLoop` (`engine.run(budget)`), `engine.probe()`,
  `Meter`/`MeteredAdapter`, the **bench** (`python -m conscio.bench`).
- *F4 "Procedural"* — `SkillLibrary` (procedural memory as **data**, not code;
  R1 intact), **Distill** (the dream's fifth sub-phase), tier-aware few-shot
  exemplars with outcome settling and a ≥50% teaching gate, skill curve in the
  bench (`--skills N`).

**Perception & plugins (v1.3)** — `conscio.perception` (`SensorAdapter`,
`PerceptionFrame`, `MockSensor`): write a sensor, and
`PerceptionFrame.to_world_state()` feeds `reflect()` unchanged. `conscio.plugins`
discovers third-party `InferenceAdapter`/`SensorAdapter`/tool plugins via entry
points (`conscio.adapters` / `conscio.sensors` / `conscio.tools`), resilient to a
broken plugin. `conscio.risk.Risk` is the shared safety-tier vocabulary.

**Structural cognition (v1.6–1.8)** — `conscio.structural` (`StructuralDistiller`
→ ranked `StructuralSignal`, pure `lookup`), `conscio.structural_consent`
(`StructuralConsent`/`ConsentScope`, `sync_structure`), `conscio.structural_drift`
(`StructuralDigest`, `StructuralDelta`/`compute_delta`, `StructuralFreshness`/
`read_head_commit`/`compute_freshness`, `StructuralDriftStore`). Engine surfaces:
`load_structure()`, `structural_lookup()`/`structural_signal()`,
`structural_delta()`/`structural_freshness()`, and the `GoalOrigin` provenance gate
+ read-only `advisory()` pull. Data, never code (R10).

**Embodiment — `conscio/mcp/` (v2.0)** — `conscio.mcp.server` (`serve`/`main`,
the `conscio-mcp` console script), `jsonrpc` (bounded-at-source frame reader,
structured errors), `protocol` (`Dispatcher`, version negotiation), `schemas`
(rigid Event schema + propose-only tool/resource defs), `seen` (`SeenStore`, the
bounded `mcp_seen.db` idempotency store). Engine pull surfaces:
`engine.propose_action(intent)` / `engine.propose_plan(goal, tools)` —
propose-only cognition composing the existing Actor/Skeptic; never execute, fail
closed without an adapter, emit a `proposal:audited` event. Nothing opens a
socket; nothing executes (act → v2.0.1).

**Society — `conscio/noosphere/` (v2.2)** — engine-free same-host skill sharing
behind `conscio noosphere {publish,import,list,show,id}`. `paths` (HERMES_HOME
layout), `identity` (`instance.json` provenance root), `artifact` (content-only
canonical hash), `catalog` (host-shared `noosphere.db`), `quarantine`
(per-instance intake), `publish` (reads the live `conscio.db` **read-only**),
`importer` (static revalidation → quarantine). Imports `goal_fingerprint` from
the `conscio.agency.fingerprint` leaf and nothing else from the engine; nothing
imported is served, executed, or promoted (mutual audit / promotion → later).

---

## Extending Conscio

Three stable extension points, usable directly or published by a third party and
auto-discovered via entry points:

```python
from conscio.plugins import discover_adapters, discover_sensors, discover_tools
# or from the CLI:  conscio plugins
```

```toml
# in your own package's pyproject.toml
[project.entry-points."conscio.sensors"]
my-sensor = "my_pkg:MySensor"        # a conscio.perception.SensorAdapter
```

Runnable examples: `examples/custom_adapter.py`, `examples/host_guardian.py`,
`examples/agent_companion.py`. Full guide: the **docs site** (see below).

---

## Bench

```bash
# offline, deterministic (MockAdapter)
python -m conscio.bench --adapter mock

# real backends (local by default)
python -m conscio.bench --adapter ollama:qwen3.5:0.8b --cycles 20
python -m conscio.bench --adapter lmstudio:qwen3.5-0.8b --cycles 20
python -m conscio.bench --adapter llamacpp --cycles 20 --json report.json
python -m conscio.bench --adapter openai:qwen3@http://localhost:8000/v1

# skill-acquisition curve (per-bucket validity / success / skill count)
python -m conscio.bench --adapter mock --skills 20
python -m conscio.bench --adapter ollama:gemma4:e4b --skills 40 --dream-every 10
```

Reports: probe profile, decode tier, per-tier syntactic validity, Skeptic
catch-rate (deterministic vs semantic), latency p50, calibration. `--skills N`
reports the per-bucket validity/success/exemplars/skill-count curve. Baselines
in `docs/bench/`.

---

## Model registry

Known models ship with the registry; unknown models are detected by context
window (`detect()` accepts a `context_window` override) or inferred from the name.

| Model | Context | Mode |
|---|---|---|
| GLM 5.1 | 131k | Compact |
| Kimi K2.6 | 256k | Standard |
| MiniMax M2.7 | 260k | Standard |
| Step Flash 3.7 | 260k | Standard |
| Nemotron 3 Super 120B | 1M | Standard |
| Claude Sonnet 4 | 200k | Standard |
| Claude Opus 4 | 200k | Standard |
| GPT-4o | 128k | Compact |
| Llama 3.1 70B | 128k | Compact |
| Qwen 2.5 72B | 131k | Compact |

```python
from conscio import ModelRegistry
ModelRegistry.register("my-model", context_window=200_000)
```

---

## Installation

```bash
pip install conscio          # from PyPI

pip install -e ".[dev]"      # from source, with the dev toolchain
pip install "conscio[docs]"  # to build the docs site (mkdocs-material)
```

Requires Python ≥ 3.10. Core depends only on `numpy`; `sqlite3` is stdlib. The
wheel ships two console scripts — `conscio` (version/info/reflect/plugins/bench)
and `conscio-bench` — and is typed (PEP 561). `dev`/`docs` extras never enter the
runtime import graph.

Docs site: guides, public-API reference, the claims ledger, and the bench reports
(built with `mkdocs build --strict`; see `docs/`).

---

## Testing

```bash
# Full suite (1437 tests) — house rule: one file per pytest process
# (low-RAM machines OOM on the full run; CI does the same)
for f in tests/test_*.py; do pytest "$f" -q; done

# Specific module
pytest tests/test_consciousness.py -v
pytest tests/test_agency_act.py -v
pytest tests/test_session_lifecycle.py -v
```

---

## Database

SQLite, WAL mode, default `~/.conscio/data/`:

```
conscio.db          # ContentStore + EventBus + ActionLedger + skills
token_tracker.db    # TokenTracker
meta_cognition.db   # MetaCognition
```

**Always** call `engine.close()` or use the `with` statement so WAL checkpoints flush.

---

## Session continuity

Seven layers of persistence (memory → agent config → skills → handoff → diary →
session DB/RAG → git). Configure your agent's hook to fire on `session:end` /
`session:reset`; Conscio runs a 6-step pipeline and writes:

- `<handoff_dir>/_latest_heartbeat.md` — compact (<1.5KB), auto-injected next session
- `<handoff_dir>/_session_handoff.md` — richer manual reference
- `<handoff_dir>/heartbeat_YYYYMMDD_HHMM.md` — dated archive

---

## Audit history

- **v2.0.1 — "Connect" (act)** — opt-in, host-executed audited `act` over MCP. A
  new `HostActChannel` (`conscio/agency/host_act.py`) audits (Skeptic) → gates
  (base `risk` + manifest `approval_policy`, plus Awake + breaker) → ledgers →
  returns an execution packet; the **host** executes and `conscio.report_result`
  closes the ledger entry (emits `act:result`, feeds breaker/trust). The five act
  tools appear only with `--enable-act`; HIGH-risk / `require_approval` stay queued
  (`conscio.pending` → `conscio.approve`). The host declares its tool manifest in
  `initialize`; `act` accepts a namespaced `idempotency_key`. Plus `conscio-mcp`
  adapter parity (six providers from config, via a shared `conscio/adapter_config.py`)
  and the **R-05** content-store chunk-dedup fix — **debt-zero**. Purely additive;
  `reflect()` untouched.
- **v2.0.0 — "Connect"** — the **Embodiment** phase. Conscio becomes embeddable
  in **any** MCP host (CLI, IDE, agent) as a live consciousness-layer via a
  hand-rolled, **stdlib-only** MCP stdio server (`conscio-mcp`, newline-delimited
  JSON-RPC 2.0): a bounded-at-source frame reader (no unbounded line buffering),
  `initialize` capability discovery + version negotiation, structured JSON-RPC
  errors. The surface is **propose-only** — tools `feed`/`note` (rigid Event
  schema, **idempotent** on `event.id`: a duplicate returns the exact prior
  result), `advisory`, `recall`, `propose_action` (Skeptic audit of an explicit
  intent), `propose_plan` (Actor generates one action against a declared tool
  vocabulary, then the Skeptic audits it); resources `advisory`/`state`/`events`/
  `handoff`; a bounded idempotency store (`mcp_seen.db`). Engine pulls
  `propose_action`/`propose_plan` compose the existing Actor/Skeptic, **never
  execute**, fail closed without an adapter, and emit `proposal:audited`. A seeded
  stdlib fuzz battery proves the transport survives hostile host input
  (malformed/oversized/partial frames, wrong version, pre-initialize) without
  hang/OOM/crash. Also paid debt-zero: atomic JSON saves for
  `world_model`/`meta_cognition`/`context_manager` (R-09), bounded quarantine
  pruning (R-02). Zero new runtime dep; nothing opens a socket; `act` over MCP →
  v2.0.1; society/noosphere → v2.1. reflect() untouched; public API unchanged.
  **1437 total.**
- **v1.9.0 — "Anneal"** — a pre-v2.0 **hardening** release; no new public surface
  (API frozen ahead of "Connect"). A bug-hunt + robustness pass making the
  corrupt/legacy/concurrent edges safe: tz-skewed earned-autonomy & quarantine
  windows fixed (naive-UTC via `timeutil`), `event_bus.query(limit=-1)` no longer
  unbounded, and the engine now **survives a corrupt/binary/legacy-incomplete
  store or state file at construction** (quarantine + recreate; every JSON loader
  degrades to a default), a NULL session title no longer blanks the handoff,
  `chunk_size<=0` no longer hangs, and the daemon heartbeat is written atomically.
  Backed by **durable guards** (`conscio.guards`: `safe_read_json`/
  `read_json_dict`/`clamp_int`) + an AST CI rule that fails on any bare
  `datetime.fromtimestamp` — turning one-off fixes into class-level prevention.
  reflect() untouched; dependency-free; debt-zero.
- **v1.8.0 — "Structural Drift"** — makes the ingested structure **temporal**.
  `conscio.structural_drift`: `compute_delta` (a pure prev→current diff — commit
  moved, content_hash changed, communities/hyperedges added·removed·resized,
  diffed by **id** so a relabel isn't drift) and `compute_freshness` /
  `read_head_commit` (graph commit vs the repo `HEAD`, read **purely from `.git`**
  — ref/packed-refs/detached/worktree, never raises, **no `git` subprocess**), with
  a corrupt-tolerant per-workspace `StructuralDriftStore`. `engine.load_structure`
  advances the baseline and emits `structure:changed` on real drift; new pulls
  `structural_delta()`/`structural_freshness()`; `advisory()["structural"]` gains
  `drift`+`freshness`; a read-only `conscio structure` CLI. reflect() untouched;
  dependency-free; debt-zero.
- **v1.7.0 — "Structural Cognition"** — the centerpiece: `StructuralDistiller`
  (`conscio.structural`) distils a Graphify `graph.json` (thousands of nodes) to
  its curated hyperedges + per-community digests, with a pure `lookup()` data
  layer. **Budget-adaptive injection** sized to the context window (~120→1200
  tokens), **additive** (the consciousness-state block byte-for-byte unchanged),
  **labels only**. **Consent-gated** ingestion (`conscio.structural_consent`,
  per-`Workspace.id`, **default OFF**, switch-safe — one project's structure never
  leaks into another). **R10 — imported cognition is data, never code**: parsed
  with `json` only, every field inert; no `networkx`, no Graphify runtime
  dependency. OOM guards (`max_bytes`/`max_nodes`) before parse. reflect()
  untouched; dependency-free; debt-zero.
- **v1.6.0 — "Structural Cognition" (field-driven slice)** — closes the
  provenance hole from the Hermes-Agent field run and turns Awake Mode into
  consumable signal. The **`GoalOrigin` provenance gate**: diagnostic goals
  (meta_error/self_prompt/compaction) never auto-run yet stay visible; a read-only
  `advisory()` consumption pull (no LLM, no mutation) surfaces state + goals
  tagged by provenance + lockdown/brake status. CI moved to Node 24. reflect()
  untouched; dependency-free; debt-zero. (Native distiller/R10 deferred to v1.7 to
  keep this release debt-free.)
- **v1.5.1 — "Awake Hardening" (patch)** — a skeptical review (not just TDD)
  hardened three live-only edges: awake survives an `act()` lockdown, the host
  port probe never raises, an awake heartbeat with no backend still reflects; plus
  sentinel/CLI/breaker fixes.
- **v1.5.0 — "Live"** — Conscio runs as a living process. **Awake Mode (R9)** —
  a persisted, default-OFF gate: the self-initiated heartbeat (`engine.run()` /
  the daemon) perceives + `reflect()`s only while asleep, full loop only when
  awake; a direct human `act()` is not gated; toggling is auditable
  (`awake:changed`). **Daemon** (`conscio/daemon.py` + `conscio-daemon`) polls a
  guarded sensor list → assembles `world_state` → `engine.run()` → `on_cycle`
  hook → workspace poll, with graceful `SIGTERM`, pidfile, and resume-from-state.
  Reference **sensors** `HostSensor` (host facts) + `AgentSensor` (peer session
  state), both read-only `Risk.LOW`, shipped as `conscio.sensors` entry points.
  **`WorkspaceContext`** detects workspace root + env class (IDE/CLI vs
  workspace-switching agents) and emits `workspace:changed`. **`OpenAIAdapter`**
  (GPT, env key) joins the OpenAI-compatible adapter that already reaches any
  custom cloud endpoint. A skeptical review (not just TDD) hardened three
  live-only edges: awake survives an `act()` lockdown, the host port probe never
  raises, an awake heartbeat with no backend still reflects. reflect() untouched,
  zero new deps, R7 intact. +67 tests. **1137 total.**
- **v1.4.0 — "Attune"** — model-context detection is offline & deterministic by
  default (known models resolve to the registry with zero filesystem/network I/O);
  config-file / LM Studio / GGUF auto-detection is opt-in (`autodetect` /
  `CONSCIO_AUTODETECT`), config is stdlib JSON (no PyYAML), GGUF array metadata no
  longer aborts the parse. Session-RAG embedder is backend-agnostic and
  dimension-safe (wrong-dim vectors dropped on write, skipped on search; re-index
  on embedder change). **Frontier inference adapters** — `AnthropicAdapter`
  (Claude) + `GeminiAdapter` (Gemini) — join the local backends (the inference
  behind Claude Code and Antigravity); R7 (no network in the ToolRegistry)
  unaffected. reflect() untouched, zero-deps core intact (stdlib `urllib`).
  +31 tests. **1070 total.**
- **v1.3.1 — "Ship" (patch)** — CLI polish: an unrecognized model now prints a
  clear note (heuristic context window + how to register) instead of falling back
  silently; `DEFAULT_MODEL` constant. `PerceptionFrame.ts` documented as epoch
  seconds (ledger convention), excluded from `to_world_state()`. Added a
  subprocess end-to-end CLI test (`python -m conscio`) and `Risk` JSON
  serialization tests. +4 tests. **1019 total.**
- **v1.3.0 — "Ship"** — Conscio becomes installable and extensible: `pip install
  conscio` (single-source version, console scripts `conscio`/`conscio-bench`, PEP
  561 typed, wheel+sdist pass `twine check`, core pulls only numpy). A public
  plugin surface — `InferenceAdapter`, the new `SensorAdapter` perception
  interface (`conscio.perception`; feeds `reflect()` untouched), and tools —
  discoverable via entry points and resilient to a broken plugin
  (`conscio.plugins`). MkDocs Material docs site (`mkdocs build --strict`).
  Release automation: tag→PyPI via OIDC trusted publishing, docs→Pages, CI build
  smoke. Examples gallery (custom-adapter, host-guardian, agent-companion). `Risk`
  unified into `conscio.risk` (re-exported; no behavior change). reflect()
  untouched, zero-deps core intact. +31 tests. **1015 total.**
- **v1.2.0 — "Prove"** — the central claim turns from machinery (Mock) into
  measurement: on `qwen3.5-0.8b` (LM Studio, CPU) execution success rose
  0.2 → 1.0 once Distill served past successes as few-shot, and the Skeptic's
  semantic catch-rate was 1.0 (`docs/bench/v1.2-skill-curve.md`,
  `docs/CLAIMS.md`). F2-deferred debt closed (empty-value validation, `fs_read`
  cap, error sanitization, `HTTPError` mapping, ledger `busy_timeout`, atomic
  `approve()` claim, lockdown-persistence e2e). Bench hardened for real backends
  (clean backend-down exit, crash-safe incremental curve). LM Studio backend
  added. reflect() untouched, zero-deps intact. +21 tests. **984 total.**
- **v1.1.0 — F4 "Procedural"** — procedural memory closes the competence loop:
  `SkillLibrary` (skills distilled from successful ledger plans; data, not code —
  R1 intact), Distill as the dream's fifth sub-phase (watermarked, last on
  purpose), tier-aware few-shot exemplars with outcome settling and a 50%
  teaching gate, skill-acquisition curve in the bench (`--skills N`), reactive
  MockAdapter. Debt paid: deprecated `datetime.utcnow()` removed repo-wide, CI
  runs tests one file at a time, mypy is a real gate, public `engine.state`.
  reflect() untouched. +48 tests. **963 total.**
- **v1.0.0 — F3 "Volition"** — the loop closes: ProbeSuite/ModelProfile
  (empirical, SQLite-cached, no hardcoded model table), schema→GBNF compiler,
  GoalArbiter, `engine.run(budget)` L3 heartbeat with binding ActBudget +
  metabolic gating, `engine.probe()`, earned L3 autonomy, Meter/MeteredAdapter,
  the bench CLI. +70 tests.
- **v1.0.0b1 — F2 "Immunity"** — semantic immune system: Skeptic, TrustMatrix,
  per-goal quarantine, risk gating, mixed-cortex audits, approval queue. 20-proposal
  adversarial suite: 100% deterministic sabotage blocked, zero executions.
- **v1.0.0a1 — F1 "Spine"** — the agency subpackage lands: contracts + zero-dep
  validator, InferenceAdapter (Mock/Ollama/llama.cpp/OpenAI-compat), OutputGateway,
  sandboxed ToolRegistry, append-only ActionLedger, minimal CircuitBreaker,
  `engine.act()` L1 PROPOSE. Safety rules amended (R3 rewritten; R6–R8 added). +83 tests.
- **v0.8.0 — Semantic Reconciliation** — contradiction detection via embedding
  antonym axes, off the hot path in the dream Reconcile sub-phase; opt-in
  non-destructive `SemanticDedup`. 56 tests. 600 total.
- **v0.7.0 — Recursive Coherence** — coherence→action loop: advisory
  `DreamRecommendation`, pure self-prompting (one bounded goal/cycle). 23 tests.
- **v0.6.0 — Coherence** — `CoherenceEngine` (epistemic/reality/ontological/
  temporal), static voice presets. 46 tests.
- **v0.5.0 — Cognitive Modes** — ShardEngine, trajectory vector, content layering. 37 tests.
- **v0.4.0 — Self-Judgment** — entropy pruning, friction, meta-reflect. 24 tests.
- **v0.3.0 — Metabolic Consciousness** — MetabolicContext + DreamCycle,
  `engine.recall()` cross-session memory, OutputFilter `DedupBlocks`+`SecretMask`. 68 tests.
- **v0.2.3 — Session lifecycle** — 6-step handoff pipeline; `session` type/category. 31 tests.
- **v0.2.0–0.2.2** — integration audits, session handoff, on-demand heartbeat injection.
- **v0.1.0 (2026-06-03)** — initial release. 313 tests.

---

## License

MIT — Neguiolidas / Neguitech
