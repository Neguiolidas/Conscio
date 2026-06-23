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
| `GET /api/health` | `{ok, version, storage, token_required}` |
| `GET /api/events?type=&category=&since=&limit=` | recent events (newest first) |
| `GET /api/actions?status=&limit=` | ledger rows (newest first) |
| `GET /api/skills?limit=` | learned skills |
| `GET /api/goals` | active goals (from `goals.json`) |
| `GET /api/state` | last state snapshot (from `state_summary.json`) |
| `GET /` , `GET /static/*` | the viewer UI (whitelisted assets only) |

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

## Not yet here

The shared **noosphere** (cross-instance catalog, quarantine, published records)
is **not** in the v2.4 Observatory — that "society view" is deferred to v2.5. The
projection is built source-pluggable so it drops in then.
