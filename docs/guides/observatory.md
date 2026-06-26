# Observatory — a read-only window into one instance

The **Observatory** (v2.4) shows one Conscio instance's persisted state — its
logs (events), goals, actions (ledger), skills, and last state snapshot — over a
**read-only** localhost viewer, plus matching read-only **MCP tools**.

It never writes, never executes, never proposes. There is no capability flag:
running the command *is* the opt-in.

## Two surfaces

```text
conscio-observatory --storage DIR [--host 127.0.0.1] [--port 8788] [--token TOK]
```

- **Viewer** (`conscio-observatory`) — an engine-free HTTP server that reads the
  instance's persisted state directly: `conscio.db` opened **read-only**
  (`mode=ro`, no `PRAGMA`, `SELECT` only) for events/actions/skills, and
  `goals.json` / `state_summary.json` parsed from the storage dir. Because it
  reads *persisted* state, it works even on a **cold** instance with no engine
  running.
- **MCP state tools** — `conscio.state`, `conscio.events`, `conscio.handoff` are
  always available on the MCP server (propose-only grade, independent of
  `--enable-act`). They re-surface the existing `conscio://` resources as
  `tools/call` entries for hosts that speak tools, not resources.

## Two freshness contracts

- **MCP tools** run inside the live engine → **live** state.
- **Viewer** reads the engine-free projection → **last-persisted snapshot**
  (may lag the live engine). The UI says so. Both are legitimate.

## Read-only by construction

- The projection opens the DB with `mode=ro`; any write raises at the SQLite
  layer. No `PRAGMA` is issued.
- The HTTP server serves **GET only** — `POST`/`PUT`/`PATCH`/`DELETE` return
  **405** on every path.

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{ok, version, storage, noosphere, liaison, token_required}` |
| `GET /api/events?type=&category=&since=&limit=` | recent events (newest first) |
| `GET /api/actions?status=&limit=` | ledger rows (newest first) |
| `GET /api/skills?limit=` | learned skills |
| `GET /api/goals` | active goals (from `goals.json`) |
| `GET /api/state` | last state snapshot (from `state_summary.json`) |
| `GET /api/daemon` | daemon liveness (from `daemon_heartbeat.json`) — see [panels](#daemon-relay-identity-panels-read-only) |
| `GET /api/relay/inbox?limit=` | this instance's liaison inbox (full payload) |
| `GET /api/identity` | this instance's identity (from `instance.json`) |
| `GET /api/society/members` | the noosphere census (see [Society view](#society-view-read-only)) |
| `GET /api/society/skills?limit=` | published skills (metadata only) |
| `GET /api/society/records?limit=` | published behavioral records (metadata only) |
| `GET /` , `GET /static/*` | the viewer UI (whitelisted assets only) |

`HEAD` is accepted on every GET path (headers only, no body).

## Security

- **Loopback-only.** The server refuses any non-loopback bind.
- **Token (optional).** Pass `--token` (or set `CONSCIO_OBSERVATORY_TOKEN`) to
  require `Authorization: Bearer <token>` on `/api/*`.
- **Multi-user hosts:** on a shared machine (e.g. a cloud VM where `ubuntu` ≠
  `root`), loopback blocks remote access but **another local user can still reach
  `127.0.0.1:<port>`**. On such hosts, **always pass `--token`**. The data served
  (ledger rationales, tool args, goal text) is more sensitive than config.

## Gating

There is **no `--enable-observatory` flag**. The `enable-*` family guards
write/execute/autonomy (`--enable-act`/`--enable-trial`/`--enable-promote`); the
Observatory has none of those. Launching the command is the opt-in; it is
independent of `--enable-act` and `--awake`.

## Society view (read-only)

The Observatory also projects the **host-shared noosphere** — the "society" of
instances that have published into `noosphere.db` (v2.5). It is read-only,
engine-free, and adds no new capability surface.

- `GET /api/society/members` — the census: each publishing instance with its
  `skills_count`, `records_count`, and `last_published_ts`.
- `GET /api/society/skills?limit=` — published skills (metadata only:
  `origin_label`, `goal_fp`, `goal_text`, `tool_seq`, `published_ts`,
  `content_sha256`).
- `GET /api/society/records?limit=` — published behavioral records (metadata
  only: `origin_label`, `entry_count`, window range, `published_ts`,
  `content_sha256`).

Artifact and bundle BLOBs are deliberately omitted — this view is an
at-a-glance census, not a content browser.

**Source.** Defaults to `$HERMES_HOME/noosphere.db`; override with
`conscio-observatory --noosphere /path/to/noosphere.db`.

**Freshness.** Society tabs show *last-published* peer state and may lag live
peers. The reader opens the db with `mode=ro` and sees the latest **committed**
rows even while a peer is writing (WAL). `immutable=1` is deliberately **not**
used — it ignores the `-wal` file and would silently return stale or empty data
under a concurrent writer.

**Read-only guarantee.** The Society reader issues no `PRAGMA`, no DDL, and no
`INSERT`/`UPDATE`/`DELETE` — `SELECT` only. SQLite's `-shm`/`-wal` handling is
an internal file-management side effect (read recovery), not a mutation of the
published data.

## Daemon, Relay & Identity panels (read-only)

Three more read-only panels (v2.8.0), all engine-free and GET-only:

- `GET /api/daemon` — the daemon's last **heartbeat** (`daemon_heartbeat.json`,
  written atomically every cycle): `ts`, `cycles`, `awake`, `pid`, `last_run`,
  `advisory`. Absent → `{"running": false}`. The UI derives staleness from `ts`.
- `GET /api/relay/inbox?limit=` — this instance's **liaison inbox**: the rows
  addressed `to_instance = <self>` in `liaison.db`, read **and** unread, newest
  first, with the **full payload**. It opens `liaison.db` with `mode=ro`
  (`SELECT` only, no `PRAGMA`) and **never marks anything read** — it is a
  viewer, not a consumer. It cannot reuse `mailbox.inbox()`, which opens the db
  read-write. Self is resolved from `instance.json`; unparseable rows are logged
  and skipped.
- `GET /api/identity` — this instance's identity from `instance.json`
  (`instance_id`, `label`, `created_ts`). Read **only** — it never calls
  `load_or_create`, so the viewer cannot mint a new identity. Absent → `{}`.

**Source.** The inbox defaults to `$HERMES_HOME/liaison.db`; override with
`conscio-observatory --liaison-db /path/to/liaison.db`. The daemon/identity reads
come from the `--storage` dir.

**Full payload, not metadata-only.** Unlike the Society view (public, BLOBs
omitted), the inbox is the operator's **private** mailbox on their own loopback
host — showing the message body is the point of an inbox viewer.

## The write counterpart (the Hub)

The Liaison **control** surface — toggling the daemon's awake state — is
write-capable, so it lives in the **Hub**, not here (v2.8.1). The Hub
(`--enable-daemon-control`) writes `daemon_control.json`; a daemon run with
`conscio-daemon --watch-control` applies it next-cycle via `engine.wake()` /
`engine.sleep()` — never signals, never `os.kill`. See the
[Hub guide](hub.md#daemon-control-opt-in-v28). The relay **inbox view** above is
read-only and stays in the Observatory.
