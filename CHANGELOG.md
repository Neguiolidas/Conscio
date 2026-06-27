# Changelog — Conscio

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.9.0] - 2026-06-27 — "Mind in the loop" (Relay Phase 3, slice 2)

### Added
- `conscio/agency/relay_cognize.py` — `cognize_respond`, an engine-cognition-routed
  relay auto-responder. The reply is generated from the agent's identity
  (`get_state_for_injection`), recalled memory (`recall`, peer text as query only),
  and advisory signal (`advisory`) plus the multi-turn transcript — its own mind,
  not a thin adapter fork.
- Daemon `--cognize` flag — rides on `--auto-respond` to select the cognition path
  (off by default; inert without `--auto-respond` + relay sensor + adapter + awake
  + `--relay-peer`).

### Integrity boundary
- Peer text NEVER enters episodic memory / the world-model / goals. `relay_cognize`
  calls only the engine read-trio; a spy-engine test (mutators raise) plus an
  import-shape test prove no mutator (`perceive`/`reflect`/`run`/`remember`) is
  ever called and the module never imports `conscio.engine`.
- Reply text capped at `max_reply_chars` (default 2000) before the 64 KB `_fit` cap,
  damping reflexive long replies between cognize peers; loop-breaker unchanged.

### Unchanged (debt-zero)
- `relay_respond.py`, all of `conscio/liaison/`, `mcp/server.py`, `mcp/protocol.py`,
  `agency/review_apply.py`, `perception/relay_sensor.py` byte-identical. No schema
  change. Liaison engine-free invariant intact (relay_cognize is *agency*).

---

## [2.8.2] - 2026-06-26 — "Conversation" (Relay Phase 3, slice 1)

### Added
- **`mailbox.thread(db, a, b, *, limit=20)`** — pure two-party history reader
  (both directions, chronological; engine-free, additive, no schema change).
- **Multi-turn relay auto-respond.** `conscio-daemon --auto-respond` now prompts
  the adapter with the conversation transcript (a `peer:`/`me:` thread, review
  rows excluded) instead of a single inbound message, char-budget clamped (the
  reader stays pure — the budget lives in the wiring).
- **`conscio.relay_broadcast`** MCP tool (under `--enable-relay`) — fan-out a
  relay message to every `--relay-peer`, best-effort per-peer result.

### Unchanged (debt-zero)
- liaison `relay.py` / `review.py`, `agency/review_apply.py`,
  `perception/relay_sensor.py`, and `mailbox.send/inbox/mark_read/purge_read`
  byte-identical. No schema change.
- daemon-perceives / server-acts boundary intact (broadcast is a mailbox write,
  never `host_act`).

---

## [2.8.1] - 2026-06-26 — "Reins" (slice 2: Hub awake control + close-out)

### Added
- **Hub awake control (gated).** `conscio-hub --enable-daemon-control` exposes
  `PUT /api/daemon/awake {awake}` and `GET /api/daemon/control`, writing
  `daemon_control.json` to the storage dir (atomic, via a new pure
  `conscio/hub/control.py`). A daemon run with `conscio-daemon --watch-control`
  reads that file at the top of each cycle and applies it via `engine.wake()` /
  `engine.sleep()`. **No signals, no PID, no `os.kill`** — the v2.7.1 C1 class
  (a toggle that killed the daemon) is structurally impossible. Double opt-in,
  both flags off by default; flag off → routes 404 and the daemon branch is
  skipped entirely (byte-identical behavior). The Hub refuses to write into a
  non-existent storage dir (500, no silent file).

### Fixed / Closed
- **install docs** now list all **six** console scripts (added `conscio-hub`,
  `conscio-observatory`).
- **CI / Release** now test the full claimed Python range (**3.10–3.13**), not
  just 3.11–3.12 — verified free of 3.11+ stdlib/syntax.

### Notes
- Debt-zero: `liaison`/`mcp`/`agency`/`noosphere`/`observatory`/`engine.py`
  byte-identical. The daemon reads the control file by name (`safe_read_json`)
  and never imports `conscio.hub` (layering).

---

## [2.8.0] - 2026-06-26 — "Reins" (slice 1: Observatory read panels)

### Added
- Observatory **Daemon** panel (`GET /api/daemon`) — reads the daemon's last
  atomic `daemon_heartbeat.json` (ts / cycles / awake / pid / last_run / advisory);
  absent → `{"running": false}`.
- Observatory **Relay** panel (`GET /api/relay/inbox`) — read-only view of this
  instance's liaison inbox via a new `LiaisonProjection` that opens `liaison.db`
  with `mode=ro` (NO PRAGMA, SELECT only, full payload, never marks read). It
  cannot reuse `mailbox.inbox()` (which opens read-write). Unparseable rows are
  logged and skipped.
- Observatory **Identity** panel (`GET /api/identity`) — reads `instance.json`
  read-only (never `load_or_create`, which would write a new UUID).
- `conscio-observatory --liaison-db` (default `$HERMES_HOME/liaison.db`).

### Notes
- Still GET-only (every mutation verb → 405), loopback-only, **engine-free**, and
  with **no `--enable` flag** — launching the viewer is the opt-in.
- The Hub awake control + daemon `--watch-control` ship as the next slice (v2.8.1).

---

## [2.7.1] - 2026-06-25 — "Hub: key vault + provider fix"

### Added
- **Hub API-Key field + key vault.** The Hub's Active Brain page now has an
  **API Key** input. On Save the raw key is written to a per-provider file under
  `~/.config/conscio/keys/` (created **0600**, dir **0700**) and the config keeps
  only an `api_key_env` reference — `validate()` still rejects a raw `api_key` in
  `config.json`, and the key is never echoed back over the API.

### Fixed
- **Provider select no longer reverts.** `resolve_provider` merges the active
  adapter's `base_url`/`api_key_env` when its type matches a builtin, so the Hub
  stops dropping a configured endpoint; the UI only seeds the form on first load
  (`_initialLoadDone`), so 3 s polls no longer clobber edits. **Test** now runs a
  parallel model probe and shows latency + a sample + detected models.

### Security
- Vault filenames are derived from sanitised inputs and validated against the
  env-var-name regex before any filesystem op (no path traversal via `type`).
- Vault files are created `0600` at open (no chmod-after-rename window); the
  presence check used by config redaction no longer mutates `os.environ`.

### Notes
- Live daemon/relay/identity **control** surface (awake toggle, relay inbox
  viewer, identity panel) is **deferred** to a later phase with its own design
  gate — it is not part of this release.

## [2.7.0] - 2026-06-25 — "Phase 2: General Relay Auto-Respond"

### Added
- **Awake relay auto-respond (daemon).** A new `--auto-respond` flag lets an
  awake daemon auto-reply to unread free-form relay messages from trusted peers,
  generating one reply each via its LLM adapter and sending it back. OFF by
  default; inert without the `relay` sensor + an adapter + `--awake` +
  `--relay-peer`. `--respond-limit` (default 10) caps adapter calls per cycle.
- Pure responder `conscio/agency/relay_respond.py` (engine-free; raw adapter
  call, no engine memory). Replies carry `auto_reply: true` + `in_reply_to`.

### Notes
- **1-turn bounded.** A peer's auto-reply (`auto_reply: true`) is consumed but
  never re-answered, so two auto-responders cannot ping-pong. Multi-turn is a
  later phase.
- **Daemon owns consumption in this mode.** The responder marks handled peer
  rows read (per-row, after each send); the host's `relay_inbox` no longer sees
  them. `RelaySensor` stays read-only.
- Debt-zero: `conscio/liaison/`, `mcp/server.py`, `mcp/protocol.py`,
  `agency/review_apply.py` unchanged. `relay_send` is a pure mailbox write, not
  `host_act` — the server is untouched.

---

## [2.6.3] - 2026-06-25 — "Ressalvas"

Post-ship hardening of v2.6.2, addressing reviewer ressalvas.

### Changed
- **Auto-review SQL is throttled.** `--auto-review` previously opened a liaison `SELECT` on every tool call; it now polls at most once per `AUTO_APPLY_THROTTLE_S` (5 s) per `Bindings`. The `host_act` gate remains the authority — this only paces the opportunistic poll.
- **`RelaySensor` id prefix widened** 8 → 12 chars in observations, to reduce visual id collision in perception/logs.

### Added
- End-to-end integration test wiring a real liaison mailbox + real `HostActChannel`/ledger + the daemon `RelaySensor` + the server `--auto-review` apply, proving the perceive → verdict → `host_act.approve` loop (previously a manual smoke).

### Notes
- **Known limitation (verdict consumption).** `review_apply.apply_verdicts` marks every polled `review_verdict` row read as bound work — including corrupt-payload, non-allowlisted, and stale-fingerprint rows. This cannot affect the normal flow (a `review_request` is published only after the act parks pending, so a peer cannot verdict before the act exists), and a corrected resend is a new mailbox row that polls fresh. A malformed verdict's original row is not re-inspectable.
- Inbox-flood backoff/stress signaling is deferred — `limit=50` already bounds per-cycle perception work.

## [2.6.2] - 2026-06-24 — "Awake Relay Sensor"

### Added
- **`RelaySensor` (daemon perception).** `conscio-daemon --sensors host,relay --relay-peer <id>` reads the liaison inbox read-only each cycle and reports unread peer messages as a `PerceptionFrame` (`relay_unread`/`review_pending` signals). It never marks messages read — consumption stays on the tool/act path.
- **`--auto-review` (MCP server).** Off by default; when the engine is awake (and `--enable-act` + `--enable-hermes-review` are on), inbound allowlisted review verdicts are auto-applied to local pending acts on each tool call — no explicit `conscio.poll_reviews` needed. The `host_act` gate remains the authority.

### Changed
- The verdict-apply core is extracted into `conscio/agency/review_apply.py` and shared by `conscio.poll_reviews` and `--auto-review` (behavior-preserving).

### Notes
- Phase 1 of "Awake Relay Sensor"; general relay auto-respond (LLM adapter) is deferred to a later phase. `conscio/liaison/*.py` remains engine-free.

## [2.6.1] - 2026-06-24 — "Relay"

### Added
- General cross-agent relay over the same Liaison mailbox: free-form directed
  messages between agents, independent of the review channel. New pure
  `conscio/liaison/relay.py` (sibling of `review.py`).
- MCP tools behind `--enable-relay`: `conscio.relay_send`, `conscio.relay_inbox`,
  `conscio.relay_read`. New CLI flags `--enable-relay`, `--relay-peer` (repeatable).
- `mailbox.purge_read()` — additive retention (read messages older than 7 days
  purged opportunistically on send; unread never deleted).

### Notes
- `--relay-peer` gates both send and receive. The two review types are reserved
  (relay never sends/surfaces them, never marks review rows read). Payloads are
  capped at 64 KB. No crypto (shared-filesystem trust domain); relay is a dumb
  pipe — never touches the engine or any act (dedup is the host's job).
- Debt-zero: no DB schema change, `send`/`inbox`/`mark_read` and the review path
  byte-identical; noosphere / Observatory / Hub untouched. Broadcast and live
  push deferred to v2.6.2 if needed.

## [2.6.0] - 2026-06-23 — "Liaison"

### Added
- Cross-agent `hermes_review`: a different agent can approve/reject a gated act
  over a shared SQLite mailbox (`$HERMES_HOME/liaison.db`). New engine-free
  `conscio/liaison/` (mailbox + pure review protocol).
- MCP tools behind `--enable-hermes-review`: `conscio.reviews`,
  `conscio.review_approve`, `conscio.review_reject` (reviewer role), and
  `conscio.poll_reviews` (proposer role; also needs `--enable-act`). A
  `hermes_review` pending act auto-publishes a directed `review_request` to each
  `--reviewer`.
- New CLI flags: `--enable-hermes-review`, `--reviewer` (repeatable), `--liaison-db`.

### Notes
- A verdict is applied only from an allowlisted reviewer and only through the
  unchanged local `host_act` gate. fp binds each verdict to one exact proposal
  (proposer id + ledger id). No crypto (shared-filesystem trust domain).
- Directed-only (no broadcast); noosphere / Observatory / Hub untouched.

## [2.5.0] - 2026-06-23 — "Society view"

### Added
- **Observatory Society view** — `conscio-observatory` gains read-only
  `/api/society/{members,skills,records}` over the host-shared `noosphere.db`,
  plus Society tabs in the viewer. Shows who published which skills / behavioral
  records (metadata only — artifact/bundle BLOBs omitted).
- `--noosphere` flag (default `$HERMES_HOME/noosphere.db`).

### Fixed
- `conscio-observatory` now answers `HEAD` requests (200, headers only) instead
  of 501 (folds in a v2.4-deferred fix).

### Notes
- Engine-free, read-only (`mode=ro`, no `PRAGMA`, `SELECT` only), loopback-only,
  GET-only (mutation → 405), no `--enable` flag (launch = opt-in). `immutable=1`
  deliberately rejected (it drops uncheckpointed WAL data).

## [2.4.0] - 2026-06-23 — "Observatory"

### Added
- **Read-only Observatory.** `conscio-observatory --storage DIR [--token TOK]`
  serves a **loopback-only** HTTP viewer of one instance's persisted state —
  logs (events), goals, actions (ledger), skills, and the last state snapshot.
  It is **engine-free** and **read-only**: a new `conscio/observatory/projection.py`
  opens `conscio.db` with `mode=ro` (no `PRAGMA`, `SELECT` only) and parses
  `goals.json` / `state_summary.json`. The server serves **GET only** — every
  mutation verb returns **405**. It reads even a **cold** instance (no engine).
- **MCP read-only state tools.** `conscio.state`, `conscio.events`,
  `conscio.handoff` are now exposed as MCP `tools/call` entries (for hosts that
  speak tools, not resources), delegating to the **same** helpers the
  `conscio://` resources use. Always on, propose-only grade, independent of
  `--enable-act`.
- **No `--enable-observatory` flag.** The Observatory has no write/execute/
  autonomy surface, so launching the command *is* the opt-in; it is independent
  of `--enable-act` and `--awake`.

### Notes
- **Two freshness contracts:** the MCP tools read the **live** engine; the
  viewer reads the **last-persisted** snapshot (may lag — the UI says so).
- **Debt-zero:** the Hub (`conscio/hub/`) is untouched; the Observatory is a
  separate, self-contained package. The shared **noosphere** view is deferred to
  v2.5 (the projection is built source-pluggable for it).
- **Multi-user hosts:** on a shared machine, pass `--token` — loopback blocks
  remote access but not other local users.

## [2.3.0] - 2026-06-23 — "Promotion"

### Added
- **Promotion-to-live.** `conscio promote --storage DIR --quarantine ROWID
  --enable-promote` graduates a quarantined foreign skill that has earned
  **≥ 3 clean local trials** (v2.2.2) into the live `SkillLibrary`, seeded with
  the counters it earned **locally** in the sandbox — never the origin's
  (stripped) stats, so no trust is inherited. Engine-side
  (`engine.promote_quarantined`); the noosphere package stays engine-free and
  read-only on `conscio.db`.
- **Tool-existence check** (deferred from v2.2). Promotion refuses a skill
  whose plan names a tool this instance's live registry does not have.
- `SkillLibrary.graft` — the single foreign-skill write seam
  (`ON CONFLICT(goal_fp, tool_seq) DO NOTHING`; never overwrites a local skill).
- Quarantine rows gain `promoted_ts` / `promoted_skill_id` (idempotency +
  audit). Promotion re-checks the content hash (tamper → refuse, no write) and
  is idempotent. `--enable-promote` is required, default off, and **independent
  of** `--enable-trial` / `--enable-act`.

---

## [2.2.2] - 2026-06-22 — "Trial / execution path"

### Added
- **Sandboxed trial of a quarantined skill.** A quarantined imported skill can
  now be replayed in a **throwaway, fs-only sandbox** to earn local,
  execution-grade pass/fail stats — `conscio trial --storage DIR --quarantine
  ROWID --model NAME --enable-trial`. The foreign plan's fixed steps run through
  the full safety stack (`validate → precheck → HIGH-block → Skeptic → dispatch`)
  against a `make_default_registry` confined to a `mkdtemp` dir (`fs_read`/
  `fs_write` only), stopping at the first failure. Binary outcome
  (`trial_successes`/`trial_failures` + `last_trial_*`) is recorded on the
  quarantine row; v2.3 promotion will read it.
- **Trial isolation.** A trial never writes the agent's `ActionLedger`, `skills`
  table, `TrustMatrix`, or trips the breaker — a foreign skill's failure cannot
  dent the local agent. The sandbox dir is always removed; tamper / corrupt-plan
  refuse **without** bumping any counter.
- **Opt-in, default off.** `--enable-trial` is required to dispatch and is
  independent of `--enable-act` (trial uses the local sandboxed registry, never
  `host_act`). Skeptic always audits foreign content (no LOW-risk fast path).
- New engine method `ConsciousnessEngine.trial_quarantined(rowid, *,
  enable_trial=False)`; new `conscio/agency/trial.py` (engine-side mechanic,
  imports nothing from `noosphere`); `noosphere/quarantine.py` gains 5 trial
  columns + an idempotent `PRAGMA table_info` migration + `record_trial` /
  `note_trial` (still engine-free). Purely additive; cognition untouched.

## [2.2.1] - 2026-06-22 — "Mutual audit"

### Added
- **Noosphere mutual audit.** An instance can publish a non-sensitive
  projection of its action ledger (`conscio noosphere publish-record`) to the
  host-shared `noosphere.db`, and a peer can independently audit it
  (`conscio noosphere audit`) — deterministic, read-only, engine-free. The
  auditor re-derives track-record, breaker quarantines, and a foreign-trust
  level under its OWN thresholds (parity-tested against the engine), and runs
  a discipline check (did the peer execute actions its own Skeptic FAILed?).
  No inherited trust; report-only; the auditor persists nothing. The LLM
  Skeptic replay is deferred to a later slice.

---

## [2.2.0] - 2026-06-22 — "Society" (Noosphere Core)

### Added
- `conscio noosphere`: share locally-proven skills across same-host instances as
  data. `publish` copies proven skills (success rate ≥ 0.5, stats stripped) into
  a host-shared `noosphere.db`; `import` pulls foreign skills into a per-instance
  quarantine after execution-free static revalidation. Also `list`, `show`, `id`.
- New engine-free package `conscio/noosphere/` (stdlib + sqlite3 only): it never
  constructs a `ConsciousnessEngine` and never imports `SkillLibrary`/`ToolRegistry`.
- Per-instance identity (`instance.json`: uuid4 + human label) as the provenance
  root; every shared skill carries origin + content hash; quarantine rows also
  record who imported it and when.
- `conscio.agency.fingerprint.goal_fingerprint` leaf module (re-exported from
  `conscio.agency.act`, behavior-identical) so non-act consumers can fingerprint
  a goal without importing the act pipeline.

### Security
- The live `conscio.db` is opened **read-only** (`mode=ro`, no `PRAGMA`, `SELECT`
  only) — the noosphere never mutates a running instance's database.
- Imported skills are quarantined, never served/executed/promoted; trust is never
  inherited. Static revalidation rejects tampered (hash mismatch), corrupt,
  malformed, `fp_mismatch`, and unknown-schema artifacts (recorded for audit).
- Zero network / socket / IPC / cross-host; the shared catalog is a local file.

---

## [2.1.0] - 2026-06-21 — "Hub"

### Added
- `conscio-hub`: localhost (127.0.0.1) stdlib HTTP control plane to swap the
  active model/provider and register custom OpenAI-compatible providers without
  editing JSON. Engine-free; config applies on next boot.
- Per-provider model auto-discovery (`/v1/models`, `/api/tags`, `/v1beta/models`)
  with free-text fallback; one-shot provider smoke test before save.
- `adapter_config` now resolves `api_key_env` (env var name) in addition to a raw
  `api_key` (additive, back-compat) — daemon + MCP inherit it.

### Security
- Hub never returns a raw API key (`redact()` drops `api_key`, masks
  `api_key_env`→`api_key_present`); `api_key_env` is validated as an env var NAME;
  127.0.0.1-only; optional `CONSCIO_HUB_TOKEN`; no exec / no SQLite write / no
  provider delete.

---

## [2.0.1] — 2026-06-20

"Connect" continued — **opt-in, host-executed audited `act` over MCP**. Conscio
audits + gates + ledgers an action and returns an *execution packet*; the **host**
executes and reports the outcome back. Conscio still never touches the world
(`registry.dispatch` is never called for a host tool). Off by default; cognition
(`reflect()`) untouched; purely additive (no existing signature changed).

### Added

- **`HostActChannel`** (`conscio/agency/host_act.py`): the host-executed action
  state machine — `propose` (Skeptic audit → risk + manifest `approval_policy`
  gate → ledger), `approve`/`reject` (the high-risk human/Hermes gate), `report`
  (closes the ledger entry, emits `act:result`, feeds breaker/trust), `pending`
  (the R6 approval queue, clamped). Reuses the existing `ActionLedger` /
  `Skeptic` / `CircuitBreaker` / `TrustMatrix` — one audit trail.
- **MCP act tools** (visible only with `--enable-act`): `conscio.act(intent)`,
  `conscio.report_result`, `conscio.pending`, `conscio.approve`, `conscio.reject`.
  The host declares its tool manifest (`name`, `params`, `risk`,
  `approval_policy`) in the `initialize` params.
- **Engine:** `enable_host_act(manifest)` (idempotent; rejects a manifest swap
  while actions are in-flight; atomic — an invalid manifest never half-enables),
  `host_act` / `host_act_error`.
- **CLI:** `conscio-mcp --enable-act` (off by default) and `--awake`. `act` and
  `approve` require the engine **Awake** and no breaker lockdown, else
  `{status: "gated"}`.
- **Idempotency:** `conscio.act` accepts an optional `idempotency_key`, namespaced
  `act:<workspace>:<tool>:<key>` and length-bounded, backed by the existing seen
  store — a duplicate returns the exact prior result.
- **Adapter parity:** `conscio-mcp` now builds the daemon's six provider types
  (`lmstudio`/`ollama`/`openai`/`anthropic`/`gemini`/`openai-compat`) from
  `~/.config/conscio/config.json` via a shared `conscio/adapter_config.py`
  (extracted from the daemon — no daemon behavior change).
- `conscio_meta` reports the real act state: `act_enabled`, `act_error`,
  `host_tools_count`, `adapter_ready`, `manifest_hash`.

### Fixed

- **R-05 (content-store dedup ignored metadata):** `ContentStore.index()` now
  re-indexes chunks under a new `category` on a content-hash match (label stays
  first-seen provenance), so a category-filtered search finds re-filed text.
  Closes the last carried v1.9 debt item — this phase ships **debt-zero**.

### Safety

- MCP stays propose-only **by default**; full `act` is opt-in, double-gated
  (`--enable-act` + Awake), host-executed (never local dispatch), ledgered per
  action, and HIGH-risk / `require_approval` / `hermes_review` actions stay queued
  for human/Hermes approval (`conscio.pending` → `conscio.approve`).

---

## [2.0.0] — 2026-06-19

"Connect" (Embodiment) — Conscio becomes embeddable in **any** MCP host (CLI,
IDE, agent) as a live consciousness-layer, via a hand-rolled **stdlib-only** MCP
stdio server. Zero new runtime dependency; nothing opens a socket. The surface is
**propose-only**: Conscio perceives, reflects, recalls, and **audits** proposed
actions, but never executes — the host stays sovereign over execution. Cognition
(`reflect()`) untouched; the existing public API is unchanged (MCP is purely
additive). Audited execution (`act` over MCP) is deferred to v2.0.1, and the
society/noosphere to v2.1.

### Added

- **`conscio-mcp` MCP stdio server** (`conscio/mcp/`): hand-rolled JSON-RPC 2.0
  over stdio with a bounded-at-source frame reader (no unbounded line buffering),
  `initialize` capability discovery + version negotiation, and structured errors.
- **Propose-only tool surface:** `conscio.feed` / `conscio.note` (rigid Event
  schema, idempotent on `event.id` — a duplicate returns the exact prior result),
  `conscio.advisory`, `conscio.recall`, `conscio.propose_action` (Skeptic audit of
  an explicit intent), `conscio.propose_plan` (Actor generates one action against
  a declared tool vocabulary, then the Skeptic audits it). Resources:
  `conscio://advisory`, `conscio://state`, `conscio://events` (query-filterable),
  `conscio://handoff`.
- **`engine.propose_action(intent)` / `engine.propose_plan(goal, tools)`** —
  propose-only cognition composing the existing Actor/Skeptic; never execute;
  fail closed without an adapter; emit a `proposal:audited` event.
- Persistent, bounded idempotency store (`mcp_seen.db`).
- `docs/guides/mcp.md` — host integration guide.

### Fixed

- **R-09 (debt-zero):** `world_model` / `meta_cognition` / `context_manager` now
  save JSON atomically (`guards.atomic_write_text`: tmp + `os.replace`) — a
  tailing reader or power-loss mid-write never sees a torn file.
- **R-02 (debt-zero):** quarantined `conscio.db.corrupt-<ts>` copies are pruned
  to the newest few instead of accumulating forever.

### Internal

- `event_bus` `VALID_TYPES` gains `proposal:audited` + `host:event` (additive).

---

## [1.9.0] — 2026-06-18

"Anneal" — a pre-v2.0 hardening release. No new public surface (the API is
**frozen** ahead of the v2.0 "Connect" phase); this is a bug-hunt + robustness
pass that makes the corrupt/legacy/concurrent edges safe. Defense-in-depth: an
invariant catalog driven as adversarial `try_break` suites, a risk-ranked
per-module battery, cross-cutting tracks, and durable guards that stop whole bug
*classes* from resurfacing. Cognition (`reflect()`) untouched; dependency-free;
debt-zero.

### Fixed

- **Earned-autonomy & quarantine-release time windows were tz-skewed** (B-003b,
  B-007). `engine._trips_since` and `agency.breaker._relevant_event_since` built
  their window boundary with naive **local** time while the event store is naive
  **UTC**, so on a non-UTC host the L3 trip window and the goal-release relevance
  window were wrong (e.g. UTC+9 counted zero recent trips). Both now go through
  the single sanctioned converter `timeutil.naive_utc_from_epoch`.
- **`event_bus.query(limit=-1)` returned the whole table** (B-004): a negative
  limit hit SQLite `LIMIT -1` = unbounded. Floored to `max(0, limit)`.
- **A corrupt or legacy store/state file crashed construction** (B-006, B-008,
  B-011, B-013). The engine now survives, at construction (I-S4):
  - a corrupt/garbage/truncated shared `conscio.db` is **quarantined**
    (`conscio.db.corrupt-<ts>`, preserved) and recreated fresh, with a
    `storage_recovered` event (B-006);
  - a binary / non-UTF-8 / non-dict JSON sidecar (`state_summary`, `world_model`,
    `meta_cognition`) falls back to defaults instead of raising (B-008);
  - a **valid-but-incomplete** `world_model.json` / `meta_cognition.json`
    (a legacy/migrated file missing keys a newer version expects) is merged over a
    default skeleton, so it can no longer `KeyError` on first use (B-011);
  - a binary / wrong-type `goals.json` / `evolution_proposals.json` (and the
    `structural_consent` / `structural_drift` / axis-pack loaders) degrade to the
    empty default — previously a `UnicodeDecodeError` escaped the narrow
    `except json.JSONDecodeError` (B-013).
- **`structural_drift.read_head_commit` raised on a binary `.git`** (B-005):
  broadened to `(OSError, ValueError)`.
- **`session_lifecycle.format_handoff` crashed on a NULL session title** (B-010):
  `None[:50]` → `TypeError`, which silently blanked the handoff and skipped the
  reflection. Guarded to match the heartbeat formatter.
- **`content_store._chunk_content` hung on `chunk_size <= 0`** (B-009): the
  paragraph-split slice never advanced → infinite loop. Floored at 1.
- **The daemon heartbeat could be read torn** (B-012): `daemon_heartbeat.json` is
  written atomically (temp + `os.replace`) so a host tailing it never sees a
  partial file.

### Internal

- **Durable guards** (`conscio.guards`, not part of the public API):
  `safe_read_json` (never-raising JSON→dict), `read_json_dict` (schema-drift-safe
  load merged over a default skeleton), and `clamp_int`. Plus an AST-based test
  rule that fails CI if any module reintroduces a bare `datetime.fromtimestamp()`
  (the tz class). These turn one-off fixes into class-level prevention.

---

## [1.8.0] — 2026-06-18

"Structural Drift" — makes the ingested structure (v1.7) **temporal**. The agent
now notices when its structural map has *drifted* (the graph was rebuilt) or gone
*stale* (the repo moved past the graph). Pure data + stdlib — **no `subprocess`,
no `git` invocation** (R10 by spirit); cognition (`reflect()`) untouched;
dependency-free; debt-zero.

### Added

- **`conscio.structural_drift`.** A pure, fail-tolerant module:
  `StructuralDigest` (the small persisted baseline of a signal), `StructuralDelta`
  + `compute_delta` (a pure prev→current diff: commit moved, `content_hash`
  changed, communities / hyperedges added·removed·resized, node/link deltas —
  diffed by **id** so a relabel is not counted as drift), `StructuralFreshness` +
  `read_head_commit` / `compute_freshness` (graph commit vs the repo `HEAD`, read
  **purely from `.git`** — ref / `packed-refs` / detached / worktree `.git`-file,
  `None` on anything unreadable, never raises), `StructuralDriftStore` (per-
  `Workspace.id` baseline, corrupt/missing/write-failure tolerant), and
  `render_delta`.
- **Drift tracking on `engine.load_structure(path, workspace_id=…, root=…)`.**
  Computes the delta vs the persisted baseline, computes freshness, advances the
  baseline, and emits a passive `structure:changed` event on real drift. New pull
  surfaces `engine.structural_delta()` / `engine.structural_freshness()`;
  `advisory()["structural"]` gains `drift` and `freshness`. Calling
  `load_structure(path)` with no `workspace_id` is byte-identical to v1.7.
- **`conscio structure` CLI.** A read-only drift + freshness report for the
  current workspace — it peeks at the baseline but never advances it, so it cannot
  mask drift from a running daemon.
- **Daemon.** `sync_structure` now passes the workspace id + root, so the
  enriched `daemon_heartbeat.json` carries drift + freshness each cycle.
- **EventBus.** `structure:changed` added to `VALID_TYPES`.

### Notes

- The numeric "commits behind HEAD" distance was deliberately **not** included:
  it would require a `git` subprocess (a new shell attack/failure surface), and
  `is_stale` drives the identical remedy (re-distill), so the count is noise. It
  may return later in an opt-in `structural_drift_extras` module if a real case
  appears.

---

## [1.7.0] — 2026-06-18

"Structural Cognition" — gives the refined model structural awareness of the
codebase it works in, distilled from a Graphify-format `graph.json`. Consumed as
data, never code (R10): no `networkx`, no Graphify runtime dependency.
Dependency-free; debt-zero. Workspace-aware, consent-gated ingestion follows in
v1.7.x.

### Added

- **`StructuralDistiller` (`conscio.structural`).** Distills a Graphify graph
  (thousands of nodes) down to its curated hyperedges plus per-community
  summaries — `distill()` returns the *full ranked* `StructuralSignal` (how much
  to inject is the consumer's budget call, not the distiller's). A pure
  `lookup(key)` data layer resolves any node / hyperedge / community id to detail
  on demand.
- **Budget-adaptive injection.** When a graph is loaded, `get_state_for_injection()`
  appends a structure block sized to the model's context window (scales from
  ~120 tokens at small contexts up to ~1200 — no hard gate). It is **additive**:
  the consciousness-state block is byte-for-byte unchanged. It renders **labels
  only**, never raw node-ids.
- **Engine pull surfaces (`advisory()` siblings — read-only, no inference).**
  `engine.load_structure(path)` ingests + distils a graph (opt-in; nothing is
  injected until called); `engine.structural_lookup(id)` drills down on demand;
  `engine.structural_signal()` returns the cached signal; `engine.unload_structure()`
  drops it. `advisory()` gains a `structural` block (`loaded`/`commit`/`hash`/
  counts, or `null`) for status + staleness detection.
- **Workspace-aware, consent-gated ingestion (`conscio.structural_consent`).**
  Which workspace's graph may be ingested is an access-control decision, made
  per-`Workspace.id` and persisted. `StructuralConsent` (`ConsentScope.OFF`/
  `PROJECT`/`PARENT`, **default OFF**), `sync_structure(engine, workspace,
  consent)`, and a `conscio consent <off|project|parent>` operator command. The
  daemon auto-loads the consented graph and re-syncs only on workspace change.

### Security

- **Switch-safe ingestion.** Switching into a workspace without consent **unloads**
  any loaded graph — one project's structure never leaks into another. Reading the
  parent multi-project folder happens only with explicit `PARENT` consent.
  Ingestion is opt-in (default OFF); a malformed graph unloads and reports rather
  than crashing the loop.
- **Provenance + staleness.** The signal carries `built_at_commit` and a sha256
  `content_hash` of the raw bytes, so a host can detect a stale graph versus the
  working tree. Conscio surfaces staleness; it never runs Graphify itself.
- **Public types:** `StructuralSignal`, `Hyperedge`, `CommunitySummary`,
  `GraphNode`, `StructuralError` (a `ValueError` subclass).

### Safety

- **R10 — imported cognition is data, never code.** The graph is parsed with
  `json` only; every field is inert untrusted data (a code-looking node label is
  returned verbatim, never executed). No `networkx`, no Graphify runtime
  dependency, no copied Graphify source — only its MIT *input format*.
- **OOM guards.** `from_path` checks file size (`max_bytes`, default 64 MB)
  *before* parsing; `max_nodes` (default 200k) caps the dominant collection;
  malformed individual items are skipped defensively rather than crashing the
  distill. Non-graph JSON raises `StructuralError`.

---

## [1.6.0] — 2026-06-17

"Structural Cognition" (field-driven slice) — turns Awake Mode from overhead
into consumable signal and closes the remaining provenance hole from the
Hermes-Agent field run. The native distiller / budget-adaptive injection (R10)
is deferred to v1.7 to keep this release debt-free.

### Added

- **`engine.advisory()` — the host pull surface (#5/#9).** A cheap, read-only,
  no-inference, no-mutation structured snapshot the host calls each turn:
  cognitive state, active goals tagged by provenance (`executable` vs
  `diagnostic`), and operational status (`action_lockdown`, last failure-rate
  `brake`). `get_state_for_injection()` (prose) is unchanged; `advisory()` is its
  machine-readable sibling.
- **Enriched daemon heartbeat (#5/#9).** `daemon_heartbeat.json` now carries the
  last-cycle run summary (`cycles`/`failures`/`stopped`) and the full advisory
  snapshot every cycle, so an out-of-process host can `tail` it for canonical,
  always-current output. Liveness keys (`ts`/`cycles`/`awake`/`pid`) are
  preserved; a failing advisory never breaks the heartbeat write.
- **Goal provenance gate (#7).** A `GoalOrigin` taxonomy decides whether the
  actor may auto-execute a goal. Externally/environmentally grounded origins
  (`user`, `internal`, `curiosity`, `anomaly`, `maintenance`) are executable;
  self-referential / error / compaction-derived origins (`meta_error`,
  `self_prompt`, `compaction`) are **diagnostic-only** — visible to the host via
  `advisory()` and injection, but the `GoalArbiter` never auto-runs them. This
  generalizes the v1.5.1 #6 slice (which removed exactly one diagnostic origin)
  into a full gate, and closes the field failure where context-compaction
  fabricated tasks auto-executed without consent. Provenance rides the existing
  `Goal.source` string, so there is **no storage migration**. A host can route a
  compaction-derived task diagnostically with
  `add_user_goal(text, origin=GoalOrigin.COMPACTION)`.
- **Integration contract + example.** New `docs/guides/integration.md`
  ("Consuming awake output") documents the pull/tail contract and the
  executable/diagnostic split; `examples/host_consumer.py` is a runnable,
  offline end-to-end host.

### Changed

- Blind-spot evolution goals (`feed_meta_to_goals`) are now tagged `meta_error`
  (diagnostic) — vague self-improvement (`Evolve: … low confidence area`) is not
  actor-actionable and stays advisory rather than feeding the act loop.
- **CI/release/docs workflows** bumped off the Node 20 runtime (force-deprecated
  2026-06-16): `actions/checkout@v6`, `actions/setup-python@v6`,
  `actions/upload-artifact@v7`, `actions/download-artifact@v8` (all Node 24).
  Inputs verified compatible against each action's pinned `action.yml`.

---

## [1.5.1] — 2026-06-17

"Awake Hardening" — fixes from the first real-world Awake Mode run (in the
Hermes-Agent environment). No new cognition; this closes correctness/safety
holes the green test suite missed and that only a live deployment surfaced.

### Fixed

- **Meta-goal feedback loop → lockdown (#6).** Recurring act errors (e.g.
  `act:tool:skeptic_fail`) were turned into an actor-executable
  `Maintenance: fix_recurring_error` goal, which the model then ran *literally*
  (`fs_read path="skeptic_fail"`) → more errors → more goals → lockdown spiral.
  Error patterns no longer mint executable goals; they remain visible through
  the existing diagnostic channels (meta-cognition, reflection, and
  `AutoEvolution.observe_errors()` — a reviewed `PATTERN_LEARN` proposal queue
  that never reaches the act pipeline). The general goal-provenance gate lands
  in v1.6.
- **Single `_RAG_DISABLED` sentinel.** The engine and content-layer each defined
  their own `object()` sentinel; tests that imported the engine's and assigned it
  to the content-layer leaked a bare object into `recall()` (swallowed by a broad
  `except`), so RAG was never actually disabled — green but wrong. The sentinel is
  now owned by `content_layer` and re-exported from `engine` (one object).
- **CLI storage no longer ephemeral.** The default storage dir was a fresh
  `mkdtemp()` per invocation, so `conscio awake` then `conscio sleep` never shared
  state. It now persists under `HERMES_HOME` (default `~/.hermes/consciousness`,
  env-overridable), consistent with `session_lifecycle`/`session_rag`.
- **CircuitBreaker quarantine-release crash.** The release path emitted an
  event of type `"status"`, which is not in the event-bus whitelist and raised
  `ValueError`. Emits a valid `"system"` event now.

### Added

- **Aggregate failure-rate brake (#8).** `ActBudget` gains `max_failure_rate`
  (default `0.5`) and `min_attempts` (default `4`); the autonomy loop stops the
  heartbeat (`stopped="failure_rate"`, plus a surfaced event) once the share of
  failed cycles crosses the threshold. This complements the per-goal
  `CircuitBreaker` (which only catches a *single* goal's consecutive failures)
  by catching broad flailing across many distinct goals/tools — the field
  failure mode. Set `max_failure_rate >= 1.0` to disable.
- **Daemon adapter wiring.** The daemon can now build its inference adapter from
  `~/.config/conscio/config.json` (or `--adapter/--base-url/--adapter-model`),
  closing the v1.5 gap where an awake daemon had no backend; the heartbeat is
  written every cycle so peers can read it.

---

## [1.5.0] — 2026-06-15

"Live" — Conscio stops being a one-shot advisory call and becomes a **living
process**: it runs continuously as a daemon, **perceives** the world through
pluggable sensors, and acts autonomously only when **explicitly awake** — a
gated state, off by default, auditable as a single switch. No new cognition:
`reflect()`, `act()`, dream, and metabolism are reused verbatim; this is the
wiring that lets the existing loop run on live input under a hard safety gate.

### Added

- **Awake Mode (R9) — the master gate for autonomous operation.** A persisted
  `awake` boolean on `ConsciousnessState` (**default OFF**), surfaced as
  `engine.awake` / `engine.wake()` / `engine.sleep()` and `conscio awake|sleep`.
  The self-initiated heartbeat (`engine.run()` and the daemon) is gated by it:
  **asleep ⇒ perceive + `reflect()` only, zero arbiter/act/dream**; awake ⇒ the
  full existing loop. A direct human `engine.act()` call is *not* gated (R9
  governs self-initiated autonomy only). Toggling emits an auditable
  `awake:changed` event; the flag survives `reflect()` rebuilds and restart.
  **R9 is the first new safety rule since R8.**
- **Reference sensors** (concrete implementations of the frozen v1.3
  `SensorAdapter`): `HostSensor` — read-only host facts (load/disk/memory/top
  processes + opt-in loopback service liveness), every probe guarded, non-Linux
  safe, `Risk.LOW`; `AgentSensor` — read another Conscio-backed agent's session
  state (open goals, last reflection, handoff) strictly read-only, `Risk.LOW`.
  Both registered as `conscio.sensors` entry points (`conscio plugins` lists
  them).
- **Daemon** (`conscio/daemon.py` + `conscio-daemon` console script). A
  persistent heartbeat: per cycle it polls a sensor **list** (each guarded — a
  failing sensor never kills the loop), assembles `world_state`, calls
  `engine.run()` (awake- and metabolic-gated), fires an `on_cycle` hook, and
  polls the workspace. Graceful `SIGTERM`/`SIGINT`, advisory pidfile
  (single-daemon-per-state-dir, stale-pid reclaim), heartbeat on shutdown, and
  resume-from-persisted-state on boot. `conscio daemon …` delegates to it.
- **`WorkspaceContext`** (`conscio/workspace.py`) — environment & workspace
  awareness: resolves the active workspace root (explicit → `CONSCIO_WORKSPACE`
  → git-root → cwd), a stable `id`, and an `EnvClass` (`STABLE` for IDE/CLI,
  `SWITCHING` for workspace-hopping agents like OpenClaw/Hermes, `UNKNOWN`
  treated as `SWITCHING`); emits `workspace:changed` when the root changes. The
  seam the v1.6 structural layer keys its per-workspace scope/consent off.
- **`OpenAIAdapter`** — GPT over the official OpenAI cloud endpoint with
  `OPENAI_API_KEY` from the env, mirroring the Claude/Gemini convenience. The
  generic `OpenAICompatAdapter` already reaches **any** OpenAI-compatible cloud
  provider (Groq, Together, OpenRouter, DeepSeek, Fireworks, cloud vLLM…) via
  `base_url` + Bearer `api_key`; `OpenAIAdapter` only pins the OpenAI default so
  the env-key read is safe (the generic adapter defaults to localhost).

### Safety

- **R9 (Awake gate)** added to the non-negotiable safety rules; asleep =
  advisory reflect-only, awake = full autonomy, default OFF. R1–R8 intact; R7
  (no network in the ToolRegistry) unaffected — the daemon perceives locally and
  acts only through the already-audited pipeline.

### Notes

- Zero new runtime dependencies (stdlib + numpy + sqlite3 core preserved).
  `reflect()` semantics unchanged. No filesystem-watcher — polling only (robust
  in containers/CI). Skeptical-review hardening folded in: `awake` survives an
  `act()` lockdown that persists a transient state; the host port probe never
  raises on a bad port; an awake heartbeat with no inference backend still
  perceives+reflects (observation is always-on).

---

## [1.4.0] — 2026-06-15

"Attune" — Conscio perceives the context window of whatever model/backend it runs
on, and the session-RAG embedder works against any OpenAI-compatible endpoint —
without making construction touch the filesystem/network by default, lose
determinism, add a dependency, or corrupt the vector store.

### Added

- **Frontier inference adapters.** `AnthropicAdapter` (Claude Messages API) and
  `GeminiAdapter` (Gemini `generateContent`) join the local adapters (Ollama,
  llama.cpp, OpenAI-compatible, LM Studio) — stdlib `urllib` only, keys from
  `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`|`GEMINI_API_KEY`. Conscio now runs on the
  backends behind **Claude Code** and **Antigravity**, removing the lock to local
  models. Inference is infrastructure, not a tool an actor can invoke, so **R7 (no
  network in the ToolRegistry) is unaffected** — this only widens the
  `InferenceAdapter` carve-out from localhost to a configured frontier API
  (operator's own key, operator's choice). Gemini context windows added to the
  registry (`gemini-2.5-pro`/`-flash`, `gemini-1.5-pro`, alias `gemini`).
- **Opt-in host-state auto-detection.** `ModelRegistry.detect(...)` and
  `ContextManager(...)` gained `autodetect` (default `False`) and `base_url`
  (default `None`). With `autodetect=True` (or env `CONSCIO_AUTODETECT`) Conscio
  consults a config file, LM Studio's active state, and GGUF metadata; an explicit
  `base_url` enables a targeted OpenAI-compatible endpoint probe.
- **JSON config** at `~/.config/conscio/config.json` (and `~/.conscio/config.json`),
  nested (`{"models": {name: {"context_window": N}}}`) or flat
  (`{"context_window": {name: N}}`).
- **Embedder identity in the vector store** — the store persists the
  `(embedding model, dim)` it was built with and triggers a clean re-index when
  the configured embedder changes.

### Changed

- **Model-context detection is offline & deterministic by default.** A *known*
  model with no explicit override resolves to the curated registry with **zero**
  filesystem/network I/O — restoring the behavior the auto-detection commits had
  broken (engine/context construction no longer scans `$HOME`).
- **Config is stdlib JSON, not YAML** — the detection path no longer depends on an
  optional PyYAML package that could silently disable the feature.
- GGUF-derived context is labelled as the **architectural max** (may exceed the
  active/loaded context); active sources (endpoint, LM Studio) are preferred.

### Fixed

- **GGUF parser** no longer aborts on array-valued metadata — it previously
  returned `None` at the first array key (e.g. tokenizer tokens) before reaching
  `context_length`.
- **Session-RAG search** can no longer crash on a dimension-mismatched vector
  (`np.dot` shape error): wrong-dim vectors are dropped on write and skipped on
  search, preventing store corruption when the embedding model changes.
- Removed a test monkeypatch that had masked the offline/determinism regression.

---

## [1.3.1] — 2026-06-14

### Changed

- **CLI** — the `info`/`reflect` model now flows from a named `DEFAULT_MODEL`
  constant, and an unrecognized model name prints a clear note (to stderr)
  explaining the heuristic context window used and how to register the model,
  instead of silently falling back.
- **`PerceptionFrame.ts`** documented as **epoch seconds** (`time.time()`,
  matching the `ActionLedger` `ts REAL` convention; `0.0` = unset) and explicitly
  excluded from `to_world_state()` so determinism holds.

### Tests

- CLI: added a subprocess end-to-end test of `python -m conscio` (covers
  `__main__.py`) and a test for the unknown-model note; version assertion is now
  bump-proof.
- `Risk`: added JSON serialization / wire-value-stability and round-trip tests.

---

## [1.3.0] — 2026-06-14

### Added

- **PyPI packaging** — `pip install conscio`. Single-source version (read
  dynamically from `conscio.__version__`), console scripts (`conscio`,
  `conscio-bench`), PEP 561 `py.typed` marker, `MANIFEST.in`, and 3.13 +
  `Typing :: Typed` classifiers. Wheel + sdist build clean and pass
  `twine check`; the published artifact pulls only `numpy`.
- **`conscio` CLI** — `version` / `info` / `reflect` / `plugins` / `bench`
  (the last delegates verbatim to `conscio.bench`). Offline-safe;
  `python -m conscio` works too.
- **Public plugin surface** — three stable, documented extension points
  discoverable via `importlib.metadata` entry points:
  - `conscio.adapters` — custom `InferenceAdapter` backends.
  - `conscio.sensors` — the new **`SensorAdapter`** perception interface
    (`conscio.perception`: `SensorAdapter`, `PerceptionFrame`, `MockSensor`);
    a sensor's `PerceptionFrame.to_world_state()` feeds `reflect()`, which is
    untouched.
  - `conscio.tools` — custom tool factories.
  `conscio.plugins` discovers them resiliently — a broken or mistyped
  third-party plugin is skipped with a warning, never crashing the host.
- **Docs site** — MkDocs Material (dev-only extra): guides, a curated public-API
  reference, the claims ledger, and the bench reports. `mkdocs build --strict`
  is green; the core stays zero-dependency.
- **Release automation** — `release.yml` (tag `v*` → gated build → PyPI via OIDC
  trusted publishing), `docs.yml` (push to `main` → GitHub Pages), and a CI
  build-smoke job. `docs/RELEASING.md` runbook.
- **Examples gallery** — `custom_adapter.py`, `host_guardian.py`,
  `agent_companion.py`: runnable, offline, each exercising one extension point
  (smoke-tested in CI).

### Changed

- **`Risk` enum** moved to `conscio.risk` as the single safety-tier vocabulary
  shared by the action and perception surfaces. `conscio.agency.tools` re-exports
  it — every historical import path resolves to the same object (no behavior
  change).

---

## [1.2.0] — 2026-06-14

### Added

- **F2-deferred hardening closed** (debt-zero before the organism):
  - `validate` rejects empty/whitespace required strings (`non_empty` rule on
    `PROPOSAL_SCHEMA.tool`).
  - `fs_read` enforces `MAX_READ_BYTES` (1 MB), matching `fs_write`.
  - Tool errors carry `Type: message` only — no traceback, no path leak.
  - `HTTPError` maps to `AdapterBadResponse` (a 4xx/5xx is a bad response,
    not a connection failure).
  - `ActionLedger` sets `busy_timeout=5000` for concurrent writers.
  - `approve()` claims the row atomically (`proposed → executing`) as the sole
    gate — a concurrent or repeated approve can never double-execute.
  - e2e test: global breaker lockdown persists across an engine restart.
- **Bench real-backend hardening:**
  - Clean non-zero exit when the backend is unreachable (no traceback, no
    report of misleading zeros).
  - Crash-safe incremental skill-curve output (atomic write after every
    bucket) tagged `complete` | `aborted`; backend death detected via the new
    `OutputGateway.last_adapter_error` signal (without changing the act
    path's `GatewayError` flow).
- **LM Studio backend** — `LMStudioAdapter` (OpenAI-compatible, default
  `:1234`) and the `lmstudio:<model>` bench spec. LM Studio rejects
  `response_format=json_object`, so the adapter omits it and lets the gateway
  drive JSON decoding (an `OpenAICompatAdapter._response_format()` hook).
- **Measured proof (v1.2 "Prove"):**
  - `docs/bench/v1.2-campaign.md` — reproducible campaign protocol.
  - `docs/bench/v1.2-skill-curve.md` + JSON artifacts — first real-backend
    measurement: on `qwen3.5-0.8b` (LM Studio, CPU) execution success rose
    0.2 → 1.0 once Distill served past successes as few-shot, and the
    Skeptic's semantic catch-rate was 1.0.
  - `docs/CLAIMS.md` — honesty ledger mapping every claim to its evidence.

### Notes

- `reflect()` untouched; zero-deps core (numpy + sqlite3) intact.
- +21 tests (**984 total**); mypy a real gate; ruff clean; per-file test loop.

---

## [1.1.0] — 2026-06-12

### Added

- **F4 "Procedural"** — procedural memory closes the competence loop
  (`success → distill → few-shot → better success`):
  - `SkillLibrary` (`conscio/agency/skills.py`) — successful audited plans
    from the ActionLedger become skills keyed by `(goal_fp, tool_seq)`,
    stored in the shared `conscio.db`. Skills are plan TEMPLATES — data,
    never code — so safety rule R1 (no autonomous self-modification) is
    untouched.
  - **Distill** — fifth dream sub-phase, after Crystallize (declarative
    consolidation precedes procedural; reads only the ledger, writes only
    skills, cannot perturb the coherence delta). Watermarked: a ledger row
    never distills twice; `dry_run` counts without writing.
    `DreamReport.skills_distilled` reports it.
  - **Few-shot in the actor** — `attach_adapter()` plugs the SkillLibrary
    into the existing `ActPipeline.few_shot_provider` hook; exemplars are
    rendered for the gateway's effective tier (KV lines for T3, JSON steps
    for T1/T2), capped at 2, gated at ≥ 50% success rate. `engine.act()`
    settles each cycle's outcome back into the served skills
    (EXECUTED rewards, FAILED penalizes, human gates never score).
  - **Skill curve in the bench** — `python -m conscio.bench --skills N
    [--dream-every K]`: per-bucket syntactic validity, execution success,
    exemplars served, cumulative skill count. Offline machinery proof via
    the new reactive MockAdapter (script entries may be callables).
- `ActionLedger`: `goal_text` column (ALTER-migrated) and
  `executed_since(after_id)`; the act pipeline now records the goal text
  on success and failure paths.
- `OutputGateway.effective_tier()`; public read-only `engine.state`
  property (the loop no longer touches `_state`).

### Fixed

- Deprecated `datetime.utcnow()` removed repo-wide — new
  `conscio/timeutil.py` `naive_utcnow()` keeps the naive ISO string format
  already stored in SQLite (the aware form would interleave `+00:00` rows).
- 14 mypy errors, including a latent `AttributeError` in
  `SessionLifecycle.record_session` (referenced `session_db`/`handoff_dir`
  that `__init__` never set).

### Changed

- CI runs pytest one file per process (house rule) with accumulated
  coverage; mypy is now a real gate (`|| true` and `continue-on-error`
  removed).

## [1.0.0] — 2026-06-12

### Added

- **F3 "Volition"** — the homeostatic loop closes
  (`sense → want → act → learn → re-sense`):
  - `ProbeSuite` / `ModelProfile` (`conscio/agency/profiles.py`) — five
    empirical micro-probes (~2k tokens: flat JSON echo, nested schema,
    enum respect, negative instruction, KV-line) measure the attached
    cortex; results cached in SQLite by model name. The profile picks
    the decode tier, the skeptic mode and the actor's tool visibility.
    No hardcoded model table. Profiles with no signal (backend down)
    are never cached and change nothing.
  - Embedded **schema→GBNF compiler** (`conscio/agency/grammar.py`) and
    **tier-1 constrained decoding** in the OutputGateway (llama.cpp
    grammar support): `tool` is locked to the registry alternation;
    one-step downgrade T1→T2/T3 per cycle.
  - **GoalArbiter** (`conscio/agency/loop.py`) — deterministic goal
    selection: generator priority × dominant-dissonance alignment (P4)
    × out of quarantine.
  - **`engine.run(budget)` (L3 heartbeat)** — reflect → arbiter/act →
    (dream when recommended) under a binding `ActBudget` (max_cycles,
    max_llm_calls, max_tokens, max_wall_s). MetabolicContext becomes a
    gate here (P3): FATIGUE halves the cycle budget, CRITICAL forces
    L1 PROPOSE. Lockdown stops the loop.
  - **`engine.probe(force=False)`** — lazy capability probing (first
    `run()` or manual; never in `reflect()`, never at attach).
  - **L3 AUTONOMOUS earned autonomy** in the TrustMatrix: calibration
    ≥ 0.75, accuracy ≥ 0.85 and zero breaker trips across the last 50
    ledger actions (`ledger.nth_recent_ts` + event-bus trip count;
    fail-safe: without trip evidence L3 is unreachable).
  - **`Meter` / `MeteredAdapter`** — inference odometer (calls, tokens,
    latency) shared by actor and skeptic adapters; makes the ActBudget
    binding and feeds the bench.
  - **Bench CLI** — `python -m conscio.bench --adapter
    mock|ollama:<m>|llamacpp[:<n>]|openai:<m>[@url]` reporting probe
    profile, syntactic validity per tier, skeptic catch-rate
    (deterministic vs semantic sabotage), latency p50 and calibration.
    Deterministic baseline published in `docs/bench/`.

### Changed

- `OutputGateway` auto-tier now selects T1 for grammar-capable adapters
  (llama.cpp); explicit `tier=` (from the measured profile) overrides.
- The ActionLedger records the real decode tier (`gateway.last_tier`)
  and the unwrapped adapter class name.
- `engine.attach_adapter` wraps the actor and skeptic adapters in a
  shared `MeteredAdapter`; `skeptic_mode` defaults to `None` (= start
  as checklist, let `probe()` pick from the measured profile);
  `autonomy_cap` now accepts 3.
- `ToolRegistry.catalog_text(max_tools)` caps the catalog for weak
  profiles (safest risks first); default remains the full catalog.
- README Safety Rule 3 final wording (Skeptic + TrustMatrix + per-goal
  quarantine made explicit).

### Notes

- `reflect()` remains untouched (P6); zero new dependencies (stdlib +
  sqlite3 + numpy). +70 new tests.

## [1.0.0b1] — 2026-06-12

### Added

- **F2 "Immunity"** — semantic immune system for the action pipeline:
  `Skeptic` (hostile-auditor clean call; binary checklist for small models,
  open critique for frontier; fail-closed), `TrustMatrix` (dynamic
  `max_action_retries` from MetaCognition calibration/accuracy, warmup
  floor, anti-deadlock probation, earned L1/L2 autonomy), per-goal
  quarantine in the `CircuitBreaker` (global lockdown only at quorum;
  recovery via cooldown or fresh relevant events), risk gating (LOW
  fast-path marked `unaudited`; HIGH always queued for humans — R6),
  mixed-cortex (`skeptic_adapter`), fs sandbox precheck before any LLM
  audit, and the `goal_update` built-in tool.
- **`engine.attach_adapter(skeptic_adapter=, skeptic_mode=, autonomy_cap=)`**
  and **`engine.pending()`** (approval queue).
- **`MetaCognition.expire_error()`** — probation recovery primitive.

### Notes

- L2 supervised execution is opt-in (`autonomy_cap=2`) and earned
  (calibration ≥ 0.6, accuracy ≥ 0.7, ≥ 10 records). Effective level is
  always `min(cap, earned)`.
- A3 acceptance: 20-proposal adversarial suite — 100% of deterministic
  sabotage blocked without LLM calls; ≥ 90% total blocked; zero executions.

## [1.0.0a1] — 2026-06-11

### Added

- **`conscio/agency/` subpackage (F1 "Spine")** — contracts + zero-dep validator;
  `InferenceAdapter` (Mock, Ollama, llama.cpp, OpenAI-compat — stdlib urllib, localhost
  defaults); `OutputGateway` with JSON repair/retry (T2) and KV-line tier for small
  models (T3); sandboxed `ToolRegistry` (fs_read/fs_write/memory_note/emit_event, risk
  levels, no network, no shell); append-only `ActionLedger` in the shared `conscio.db`;
  minimal `CircuitBreaker` (fixed threshold until the F2 TrustMatrix).
- **`engine.act()` (L1 PROPOSE)** + `engine.attach_adapter()` / `approve()` / `reject()`.
- **`ConsciousnessState.action_lockdown`** persisted via `save_state`/`load_state`.
- **`ModelInfo.has_json_mode` / `ModelInfo.supports_gbnf`** capability flags.
- **README Safety Rules amended** — R3 rewritten for the audited action pipeline; R6–R8 added.

### Notes

- `reflect()` untouched (advisory core preserved). Zero new dependencies.

## [0.9.1] — 2026-06-10

### Fixed

- **`session_rag` property lazy re-initialization** — Setting `engine._session_rag = None` was
  insufficient because the `session_rag` property would re-create a SessionRAG on access. Added
  `_RAG_DISABLED` sentinel class attribute; all test fixtures updated to use it.
  (Fixes `test_recall_graceful_when_rag_unavailable` — 707/707 tests passing.)

### Added

- **CI workflow** — `.github/workflows/ci.yml` with pytest (3.11/3.12 matrix), coverage, and ruff lint.
- **`project.urls`** in pyproject.toml — Homepage, Repository, Issues, Changelog.
- **`ruff` and `mypy`** in dev dependencies.
- **`.gitignore`** — added `*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`, `.ruff_cache/`, `.mypy_cache/`.
- **`CONTRIBUTING.md`** — development workflow, TDD, PR checklist.
- **`SECURITY.md`** — vulnerability reporting policy.

---

## [0.9.0] — 2026-06-09

### Added

- **Wiring sprint (4 work-packages)**:
  - W1: Metabolic assessment wired into `engine.reflect` state injection.
  - W2: `ContentLayerManager` wired into engine for `recall`/`perceive` delegation.
  - W3: `SessionLifecycle` wired into engine for session persistence hooks.
  - W4: `output_filter` wired into `session_lifecycle` for clean handoff/heartbeat.
- **Test coverage expansion** — new tests for ContentLayerManager, ContentStore FTS internals,
  SessionRAG coverage, GoalGenerator (user goals, cancel, expire), MetaCognition (outcomes,
  critiques), OutputFilter pipeline config, Engine propose_evolution, Models registry, and more.
- **`numpy` added as explicit dependency** in pyproject.toml.
- **Configurable `handoff_dir` and `session_db`** — optional handoff, no longer hardcoded.
- **Shared `SessionRAG` factory module** — `session_rag_factory.py` replaces fragile `__import__` lambda.
- **`k` parameter validation** in `ContentLayerManager.recall()`.

### Fixed

- **`UnboundLocalError` in `record_session_lifecycle`** — initialize heartbeat/handoff before try block.
- **Debug logging** added to bare `except Exception` blocks in session_lifecycle.py.
- **Local-only files removed from git tracking**, `.gitignore` updated.

---

## [0.8.0] — 2026-06-08

### Added

- **Semantic Reconciliation** — contradiction detection and resolution in dream cycle:
  - `axis_pack` — antonym-axis preset loader (e.g. risk/safety, exploration/exploitation).
  - `SemanticEngine` — antonym-axis polarity via pole projection.
  - `ContradictionDetector` — lexical fast-path then axis opposition.
  - `world_model` public `relation`/count reads, `state_log`, contradiction cache.
  - `dreaming` Reconcile sub-phase — semantic contradiction marking.
  - `output_filter` `SemanticDedup` — annotate near-dup blocks, never merge.
- **E2E test** — dream Reconcile + ontological recovery.

### Changed

- `coherence.ontological_score` reads cached flags instead of `world._data` directly.

---

## [0.7.0] — 2026-06-07

### Added

- **Hook-based handoff** — `session:start` injection hook for agent framework integration.
- **Session handoff architecture** documentation.

### Fixed

- **Coherence format guard** — `session_lifecycle` now guards against non-float types (MagicMock, str).

---

## [0.6.0] — 2026-06-07

### Added

- **Voice Presets** — configurable voice/personality profiles for agent communication.
- **ContentLayerManager** — unified content operations (recall, perceive) with session RAG integration.

---

## [0.5.0] — 2026-06-06

### Added

- **Cognitive Modes**:
  - `Shard` enum + deterministic `infer_shard` + `ShardEngine` for cognitive mode switching.
  - `ConsciousnessState.shard` field + injection line in context_manager.
  - Engine infers active shard in `reflect()`, surfaces in state.
  - `TrajectoryVector` — directional cognitive momentum tracking.
  - Shard-aware reflection with shard field in injection.

### Fixed

- **Shard computation timing** — compute shard at reflect-entry, before self-emitted events.
- **`world_model.list_entities`** — revived dead `enrich` call.

---

## [0.4.0] — 2026-06-05

### Added

- **Self-Judgment (entropy + friction + meta-reflect)**:
  - `world_model.entropy(name)` — age/isolation/relevance disorder score.
  - `world_model.prune_by_entropy` + `recently_changed`.
  - `world_model` bounded prediction-error log + `recent_prediction_error_rate`.
  - `dreaming` entropy Prune + Friction (Release → Prune → Friction → Crystallize).
  - `context_manager` `ConsciousnessState.reflection_quality` field + injection line.
  - Engine advisory `meta_confidence` on reflect (Witness loop).
- **Friction matching hardening** — whole-word + min-length guard.

### Fixed

- **Exact `dry_run` parity** in `prune_by_entropy`.
- **Dream prune test** updated to entropy contract (age-compounded staleness).

---

## [0.3.0] — 2026-06-05

### Added

- **Metabolic Consciousness**:
  - `Noosphere` tier model + `ContextManager.metabolic_state`.
  - `DreamCycle` orchestrator + `engine.dream()`.
  - `session_rag` — injectable embedder + `available()` probe + tests.
  - `engine.recall()` — cross-session memory + reflect past-context injection.
  - `output_filter` `DedupBlocks` + `SecretMask` stages, wired into engine.
  - `event_bus.purge_duplicates` — all-time exact-dup collapse.
  - `world_model.prune_stale` — decay then purge stale entities + relations.
  - `session` trigger `dream()` after handoff (Mitosis → Dream).
- **Performance regression guard** — reflect/dream at 10k events + 1k entities.

### Fixed

- **3 bugfixes**: OutputFilter config keys, SessionRAG fallback, EventBus dedup edge case.

---

## [0.2.3] — 2025-06-05

### Added

- **Conscio ↔ agent framework session lifecycle integration** — `record_session_lifecycle`, `format_heartbeat`,
  `format_handoff`, `enrich_with_conscio`, `get_latest_session`.
- **SessionRAG** — semantic search over session DB via Ollama embeddings.
- **AGENTS.md** — boot instructions for AI agents working on Conscio.

---

## [0.2.1] — 2025-06-05

### Fixed

- **OutputFilter pipeline config keys** — `build_pipeline_from_dict` called with incorrect dict keys
  (`"max"` → `"max_lines"`, `"max_chars"` → `"max_width"`).
- **ConsciousnessEngine missing lifecycle cleanup** — added `close()` (idempotent) + context manager.
- **Dead import in `reflect.py`** — removed unused `build_pipeline_from_dict` import.

### Added

- **3 regression tests** for v0.2 fixes (316 tests total).

---

## [0.2.0] — 2025-06-04

### Added

- **ContentStore** — FTS5 BM25 dual-index with RRF merging.
- **EventBus** — deduplicated event bus with SHA-256 content hashing.
- **OutputFilter** — 8-stage text compression pipeline.
- **TokenTracker** — token estimation with per-source tracking.
- **Migrator** — JSON → SQLite migration tool.
- **Engine v0.2 integration** — all v0.2 modules in the `reflect()` pipeline.

---

## [0.1.0] — 2025-06-03

### Added

- **ConsciousnessEngine** — central orchestrator.
- **ContextManager** — mode detection and context budget allocation.
- **ModelRegistry** — model → context → mode mapping.
- **WorldModel** — entity/relation store with predictions and temporal decay.
- **MetaCognition** — confidence tracking, blind spot detection, error patterns.
- **GoalGenerator** — drive-based goal generation with meta-score computation.
- **AutoEvolution** — safe self-modification proposals with human approval gates.
- **InnerMonologue** — reflection/observe/summarize loop.
- 313 tests across 6 test files.
