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
