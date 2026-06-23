# Liaison — cross-agent review (v2.6.0)

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
