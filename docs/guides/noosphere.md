# Noosphere — sharing skills across instances

The **noosphere** lets two or more Conscio instances on the **same host** share
their locally-proven skills as **data**. One instance *publishes* its proven
skills to a shared catalog; another *imports* them into a local **quarantine**.

Nothing imported is trusted, served, executed, or promoted. v2.2.0 builds the
data plane only — a foreign skill arrives as inert, audited data.

## Guarantees

- **Engine-free.** The `conscio noosphere` commands never construct a
  `ConsciousnessEngine` and never touch the live skill library.
- **Read-only on your live DB.** Publishing opens your `conscio.db`
  **read-only** (`mode=ro`, no `PRAGMA`, `SELECT` only). The noosphere never
  writes to your live database.
- **Zero network.** The shared catalog is a local SQLite file. No socket, no
  IPC, no cross-host.
- **Trust is never inherited.** Imported skills land in a separate quarantine
  store with their stats stripped; they are never served or executed.

## Storage layout

| Path | Scope |
|------|-------|
| `$HERMES_HOME/consciousness/conscio.db` | per-instance live DB (read-only to the noosphere) |
| `$HERMES_HOME/consciousness/instance.json` | per-instance identity (uuid + label) |
| `$HERMES_HOME/consciousness/noosphere_quarantine.db` | per-instance import quarantine |
| `$HERMES_HOME/noosphere.db` | **host-shared** catalog (override with `--noosphere`) |

`$HERMES_HOME` defaults to `~/.hermes`. Select a specific instance with
`--storage`.

## Commands

```text
conscio noosphere publish [--storage DIR] [--noosphere PATH]
conscio noosphere import  [--storage DIR] [--noosphere PATH]
conscio noosphere list    [--storage DIR] [--catalog]
conscio noosphere show    (--quarantine ROWID | --catalog ORIGIN_ID SHA256)
conscio noosphere id      [--storage DIR] [--set-label NAME]
```

- **publish** — copy this instance's *proven* skills (success rate ≥ 0.5) into
  the shared catalog. Idempotent; local stats are stripped before sharing.
- **import** — read other instances' catalog entries, statically revalidate
  each, and record it in the local quarantine as `quarantined` (passed) or
  `rejected` (with a reason). Importing your own skills is skipped.
- **list** — show the local quarantine, or `--catalog` for the shared catalog.
- **show** — print one quarantine row or one catalog entry in full.
- **id** — show this instance's identity, or rename it with `--set-label`.

## Static revalidation

On import, each artifact is checked **without executing anything**:

1. **content hash** — the stored bytes must hash to the recorded
   `content_sha256` (else `tampered`).
2. **shape** — must decode as a JSON object with the right fields/types (else
   `corrupt` / `malformed`).
3. **schema** — the artifact schema version must be understood.
4. **fingerprint** — the goal fingerprint is recomputed locally and must match.
5. **consistency** — the plan's tools must equal the declared tool sequence.

Rejected imports are still recorded for audit; they are never served, executed,
or promoted.

## Two-instance example

```bash
# instance A publishes its proven skills to the shared catalog
conscio noosphere publish --storage /tmp/a --noosphere /tmp/noosphere.db

# instance B imports them into its local quarantine
conscio noosphere import  --storage /tmp/b --noosphere /tmp/noosphere.db

# inspect what B received
conscio noosphere list --storage /tmp/b
conscio noosphere show --storage /tmp/b --quarantine 1
```

## Provenance

Every shared skill records where it came from (`origin_instance_id`,
`origin_label`), when it was published, and its content hash. Each quarantine
row also records who imported it and when. Provenance is attribution and
tamper-evidence — not authorization. The real gate is local revalidation.

## Mutual audit (v2.2.1)

Publishing a skill is one thing; deciding to trust the instance behind it is
another. v2.2.1 lets an instance publish a **behavioral record** and lets peers
**audit** it independently.

```bash
# On instance A — publish a non-sensitive projection of your action ledger
conscio noosphere publish-record

# On instance B — audit every peer's latest record (read-only)
conscio noosphere audit
```

The bundle carries only `{seq, ts, goal_fp, tool, tier, status, ok, verdict}` —
never arguments, outputs, errors, rationales, or goal text. The auditor
re-derives, under **its own** thresholds: per-tool accuracy, the goals it would
itself have circuit-broken, a foreign-trust level (L1/L2/L3), and a discipline
check (was anything executed despite the peer's own `FAIL` verdict?). It prints
a verdict per peer — `TRUSTED` / `SUSPECT` / `REJECTED` / `INSUFFICIENT` — and
**persists nothing**. No trust is inherited; the peer's own recorded verdicts
are treated as claims to check, not as authority.

## Trial (v2.2.2)

Importing a skill leaves it inert — quarantined data with no local track
record. v2.2.2 lets a quarantined skill **prove itself locally** before any
promotion, by replaying its plan in a throwaway sandbox.

```bash
conscio trial --storage DIR --quarantine ROWID --model NAME --enable-trial
```

The foreign plan's **fixed** steps are replayed — no actor, no decode — through
the full safety stack: argument validation, the sandbox precheck, a HIGH-risk
block, the engine's **own** Skeptic (forced — foreign content gets no LOW-risk
fast path), then dispatch. Dispatch runs against a registry confined to a
**disposable directory** exposing only `fs_read`/`fs_write`; the directory is
deleted afterwards, so a trial leaves no trace on disk. The run **stops at the
first failing step** and records a binary outcome on the quarantine row
(`trial_successes` / `trial_failures` + `last_trial_result` / `last_trial_error`).

Guarantees:

- **Isolated.** A trial never writes the live agent's action ledger, skill
  library, trust matrix, or circuit breaker. A foreign skill failing its trial
  cannot dent the local agent's reputation or autonomy.
- **Opt-in, default off.** `--enable-trial` is required; it is **independent of**
  `--enable-act` (a trial uses the local sandbox, never the host-executed `act`
  channel).
- **Tamper-safe.** Before spending an LLM call the artifact's content hash is
  re-checked; a mismatch (or a corrupt plan) **refuses** and records a note
  *without* bumping any counter.

A trial only reaches as far as the tools you locally have: a plan that names a
tool the sandbox doesn't expose fails at that step (recorded), and a plan that
reads a file it didn't itself create fails in the empty sandbox — by design, a
plan that needs a pre-existing environment can't be vouched for in isolation.
The pass/fail counts this produces are exactly what the **promotion** step
(v2.3, below) reads before graduating a foreign skill into the live library.

## Promotion (v2.3)

A trial earns a quarantined skill a local pass/fail record; it stays inert
until **promotion** graduates it into the live `SkillLibrary`.

```bash
conscio promote --storage DIR --quarantine ROWID --enable-promote
```

Promotion is a pure data gate — no LLM, no sandbox, no execution. It graduates
a row only when it has earned **≥ 3 clean local trials** (`trial_successes ≥ 3`,
`trial_failures == 0`) **and** every tool its plan names exists in this
instance's live registry. The grafted skill is seeded with the counters it
earned locally in the sandbox — never the origin's (stripped) stats — so no
trust is inherited; once live, the normal act/settle loop scores it and
`MIN_SERVE_RATE` benches it if it drops below 0.5.

Guarantees:

- **Engine-free noosphere preserved.** The write lives engine-side
  (`engine.promote_quarantined`); `conscio/noosphere/` stays read-only on
  `conscio.db`.
- **Never overwrites a local skill.** A `(goal_fp, tool_seq)` collision with an
  existing skill is refused — foreign data never clobbers home-grown memory.
- **Tamper-safe + idempotent.** The content hash is re-checked before any
  write; an already-promoted row (`promoted_ts`) refuses. Unlike a trial, a
  refused promotion records **nothing** on the quarantine row — promotion only
  ever writes on a successful graft.
- **Opt-in, default off.** `--enable-promote` is required and independent of
  `--enable-trial` / `--enable-act` — promotion mutates the agent's live
  procedural memory, the most consequential of the three writes.
