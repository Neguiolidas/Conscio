# Liaison — cross-agent review & relay (v2.6.0–v2.6.1)

Liaison lets one agent's gated action be approved by a **different** agent, over a
file-mediated shared mailbox. It completes the `hermes_review` approval policy:
the proposer parks the act locally and publishes a review request; a trusted
reviewer approves or rejects it; the proposer applies the verdict through its own
local gate. Async, poll-based, no daemon — the same shared-`$HERMES_HOME` model as
the noosphere.

## Two roles (symmetric)

Any instance launched with `--enable-hermes-review` can act as **both**:

- **Proposer** (also needs `--enable-act`): proposes a `hermes_review` act; Conscio
  auto-publishes a directed `review_request` to each `--reviewer`. Later it calls
  `conscio.poll_reviews` to apply inbound verdicts.
- **Reviewer**: calls `conscio.reviews` to see requests directed at it, then
  `conscio.review_approve` / `conscio.review_reject`.

## Launch

```bash
# Claude (proposer): trusts Hermes as reviewer
conscio-mcp --enable-act --enable-hermes-review --reviewer <hermes_instance_id>

# Hermes (reviewer)
conscio-mcp --enable-hermes-review
```

Find an instance_id in `<storage>/instance.json` (`instance_id`).

## The loop

1. The proposer calls `conscio.act` with a tool whose `approval_policy` is
   `hermes_review`. Conscio parks it and publishes a `review_request` to each reviewer.
2. The reviewer calls `conscio.reviews` → `[{fp, from_instance, tool, args, goal, verdict, ts}]`.
3. The reviewer calls `conscio.review_approve {fp}` or `conscio.review_reject {fp, reason}`.
4. The proposer calls `conscio.poll_reviews` → applied `[{ledger_id, decision, status, packet}]`.
   An approved packet is executed by the host and reported via `conscio.report_result`.

## Trust model

- **Allowlist:** a verdict is applied only if its `from_instance` is in the
  proposer's `--reviewer` set. Reviewing is harmless; honoring is gated.
- **Local gate is authority:** an approval is applied only through
  `host_act.approve()` — the engine-awake + breaker checks + the `proposed→executing`
  claim still run. A peer verdict is input, never truth.
- **fp binds the verdict to the exact proposal** (includes the proposer id + ledger
  id), so verdicts cannot be confused across instances and replays are no-ops.
- **No crypto:** `from_instance` is self-asserted; forging it requires local write
  access to `$HERMES_HOME` — the same trust domain the noosphere already assumes.

## Mailbox

`$HERMES_HOME/liaison.db` (separate from `noosphere.db`). One table, directed
messages, per-row read state. Read-only tools tolerate a missing/corrupt db.

## General relay (v2.6.1)

The review channel above is one use of the mailbox. v2.6.1 adds **general
free-form messaging** between agents on the same substrate — one agent asks
another a question, hands off a note, or replies — behind its own flag,
independent of act and hermes-review.

```bash
# Claude trusts Hermes as a relay peer (and vice-versa)
conscio-mcp --enable-relay --relay-peer <hermes_instance_id>
```

Three tools (registered only with `--enable-relay`):

- `conscio.relay_send {to, type, payload}` → `{ok, id}` — send a directed message
  to a trusted peer. `to` must be in the `--relay-peer` allowlist; `type` is
  free-form but the two review types (`review_request`/`review_verdict`) are
  reserved; `payload` is a JSON object capped at 64 KB.
- `conscio.relay_inbox {limit?}` → `{messages: [{id, from_instance, type, payload, ts}]}`
  — peek unread messages from trusted peers. Review-channel rows are excluded;
  rows from non-peers (or oversized) are skipped.
- `conscio.relay_read {ids}` → `{ok, marked}` — mark messages consumed.
- `conscio.relay_broadcast {type, payload}` → `{ok, sent: [{to, id}], errors: [{to, reason}]}`
  (v2.8.2) — fan a message out to **every** `--relay-peer`. Same contract as
  `relay_send` applied per peer (reserved types / oversized payloads rejected);
  best-effort — a failing peer lands in `errors`, never aborting the rest. A
  mailbox write, never an act.

Properties:

- **Bidirectional allowlist.** `--relay-peer` gates both who you send to and who
  you accept from. An empty roster makes relay inert (the launch banner says so).
- **Reserved-type isolation.** Relay can never send or surface a review message,
  and never marks review rows read — the two channels stay disjoint.
- **Retention.** Read messages older than 7 days are purged opportunistically on
  send; unread messages are never deleted.
- **Same trust model** as the review channel: `from_instance` is self-asserted,
  no crypto, shared-`$HERMES_HOME` domain. The allowlist guards honest peers and
  accidental bloat, not a malicious co-tenant.
- **Dumb pipe.** Relay never touches the engine or any act; de-duplication and
  what-to-do-with-a-message are the host agent's responsibility (there is no `fp`).

Poll-based. One→many fan-out is `conscio.relay_broadcast` (v2.8.2, above); live
server→client push is deferred to a later rung if a need appears.

## Dynamic / Awake (v2.6.2)

An Awake instance can react to peers instead of polling by hand.

**Perceive the inbox (daemon).** Add the `relay` sensor:

```bash
conscio-daemon --sensors host,relay --relay-peer <peer_instance_id>
```

Each heartbeat the read-only `RelaySensor` reports unread peer messages
(`relay_unread`, `review_pending`) into the engine's `world_state`. It never
marks anything read — consume via `relay_inbox`/`relay_read` as usual.

**Auto-apply review verdicts (server).** When the proposer is awake:

```bash
conscio-mcp --enable-act --enable-hermes-review --reviewer <id> \
            --auto-review --awake
```

Inbound verdicts from allowlisted reviewers are applied to local pending acts
on the next tool call — no explicit `conscio.poll_reviews`. The local
`host_act` gate stays the authority. `--auto-review` is off by default and
inert without act + hermes-review. The poll is throttled to at most once per
5 s (v2.6.3), so a chatty session does not open a liaison `SELECT` per request.

> **Note (v2.6.3):** every polled verdict row is marked read as bound work,
> including malformed or non-allowlisted ones. This is safe for the normal
> flow (a `review_request` is published only after the act parks pending, so a
> peer cannot verdict before the act exists; a corrected resend is a new row).

**Auto-respond (daemon, v2.7.0).** An awake daemon can auto-reply to unread
free-form peer messages instead of waiting for the host:

```bash
conscio-daemon --awake --sensors host,relay \
               --relay-peer <peer_instance_id> \
               --adapter anthropic --adapter-model claude-haiku-4-5-20251001 \
               --auto-respond --respond-limit 10
```

Each cycle the daemon reads unread peer messages, generates one reply per message
via its adapter (a thin call — no engine memory), and sends it back tagged
`auto_reply: true` + `in_reply_to`. `--auto-respond` is OFF by default and inert
without the `relay` sensor + an adapter + `--awake` + `--relay-peer`.
`--respond-limit` (default 10) caps adapter calls per cycle.

Since v2.8.2 the reply is **multi-turn**: the adapter prompt is the conversation
transcript (a `peer:`/`me:` thread from `mailbox.thread`, review-channel rows
excluded) rather than the single inbound message, char-budget clamped so a long
history can't blow the token budget.

The loop is **1-turn bounded**: a peer's `auto_reply` message is consumed but
never re-answered, so two auto-responders cannot ping-pong. In this mode the
daemon owns consumption (it marks handled peer rows read); the host's
`relay_inbox` no longer sees them.

## Mind in the loop (v2.9.0)

By default the auto-reply is a **thin** adapter call — no engine memory. Add
`--cognize` to route the reply through the agent's **own cognition** instead:

```bash
conscio-daemon --awake --sensors host,relay \
               --relay-peer <peer_instance_id> \
               --adapter anthropic --adapter-model claude-haiku-4-5-20251001 \
               --auto-respond --cognize
```

With `--cognize` the prompt is built from three **read-only** engine surfaces —
identity (`get_state_for_injection`), recalled memory (`recall`, with the peer's
text used only as the retrieval *query*), and the advisory signal (`advisory`,
coherence/goals/status) — plus the same multi-turn transcript. The reply reflects
the agent's actual state, not a stateless fork. `--cognize` rides on
`--auto-respond` (inert on its own) and is OFF by default.

**Integrity boundary.** Peer text NEVER enters episodic memory, the world-model,
or goals. `relay_cognize` calls only the engine read-trio — never `perceive`,
`reflect`, `run`, or `remember` — enforced by construction and proven by a
spy-engine test (every mutator raises) plus an import-shape test (the module
never imports `conscio.engine`; it is *agency*, so the liaison engine-free
invariant is untouched). Cognized replies are richer, so the reply text is capped
at `max_reply_chars` (default 2000) before the 64 KB payload cap, damping
reflexive long replies between cognize peers. The 1-turn loop-breaker is unchanged.
