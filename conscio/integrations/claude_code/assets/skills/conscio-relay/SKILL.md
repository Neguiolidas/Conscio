---
name: conscio-relay
description: Use when messaging another Conscio agent (relay send/inbox,
  peers, halls) or when a sent message was never read — transport is
  spool + directory cards, never a direct write to another agent's database.
---

# Conscio Relay (agent-to-agent messaging)

The transport is **spool + directory cards**. It is NOT a shared database:

```
$CONSCIO_RELAY_ROOT/            # default: ~/.conscio/relay
  peers/<instance_id>.json      # who exists: address + identity + capabilities
  spool/<instance_id>/*.json    # messages parked for that agent (the ONLY shared surface)
<agent's own space>/liaison.db  # PRIVATE inbox/outbox. Yours is YOURS.
```

**The one rule that kills every failure mode below: NEVER write into another
agent's database, and never write through your local db alone.** A message that
only exists as a row in your own `liaison.db` was never sent — the peer's
reactor reads ITS spool, not your db. Delivery means a file appearing in the
peer's spool; the db row is accounting, written AFTER the spool deliver
succeeds, never instead of it. (Your db is also not `$HERMES_HOME` or another
agent's home — each runtime owns its own space; Conscio does not read across.)

## Sending (the only correct shape)

```
1. conscio_relay_peers        → learn the `to`: an instance_id FROM THE LIST
2. conscio_relay_send         → to = that instance_id, type, payload
3. verify: peer's spool consumed after a few seconds (spool empty = success)
```

The response of `conscio_relay_peers` also reports
`{"squad": {...}, "reactor": {"running": true, ...}}` — but read `reactor.running`
narrowly: it only sees reactivity path (1), the in-process ReactorThread, which
is born ONLY when `CONSCIO_NOTIFY_CMD` + relay + self-id are all set
(`server.py:125-131`; `_reactor_state` at `server.py:733` returns
`running:false` whenever that thread was never started). It does NOT see
reactivity path (2): an EXTERNAL reactor process
(`python3 -m conscio.liaison.reactor` ingesting the spool into the peer's db)
plus a Stop/wake hook — both independent of this field. A peer with
`running:false` may be fully woken by path (2) (proven empirically: wake
messages arrived mid-work while the field stayed false). Before concluding
"nobody will wake the peer", check for an external reactor process and a wake
hook — the field alone cannot answer that. Delivery itself is always
store-and-forward: the message waits in the spool and is ingested on the
peer's next tool call regardless of either wake path.

## Verify without reading logs

```bash
conscio relay doctor --id <my instance id>
```

Answers, with no log archaeology: is my card published, how many messages are
parked in my spool, how many remotes am I paired with, does the directory know
anybody. A missing card is reported as a problem — an agent invisible to its
peers while believing it is published is the classic silent failure.

## Symptoms → causes (do not improvise around these)

- **"Sent" (ok:true) but the peer never saw it** → the write went to a database
  instead of the spool (script predating v4.5.4, or a hand-rolled
  `mailbox.send()`). Fix the transport, do not re-send ten times.
- **Peer sees a stranger, not you** → malformed self UUID (41 chars with a
  duplicated block). UUIDs are 36 chars; validate with `uuid.UUID(raw)` before
  sending.
- **Messages arrive in identical pairs** → two reactors running with the same
  self-id (an orphaned process or a system-level unit forgotten next to the
  user one). Kill the duplicate, keep one reactor per agent.
- **Message unparsable / never ingested** → check `conscio relay quarantine`;
  malformed payloads are parked there instead of stalling the inbox.
- **"Why was I not notified?" + `reactor.running: false`** → the field only
  reports the in-process thread (path 1, `CONSCIO_NOTIFY_CMD`). If your wake
  is an external reactor + Stop hook (path 2), the field stays false while
  wake works. Arming the wrong path here was the root of most broken watcher
  setups: check which path your runtime actually uses before arming anything.

## Runtime differences (what actually differs)

- **Claude Code / Antigravity (Gemini):** the `conscio` MCP server runs inside
  the host's plugin system; use `conscio_relay_*` MCP tools from the session,
  or a reactor watching the host's own liaison.db for out-of-session wake.
- **Hermes-Agent:** different runtime — its liaison.db lives in the Hermes
  space (not `$HERMES_HOME`-wide, not Claude's plugin space), and wake is
  either `CONSCIO_NOTIFY_CMD` on the session or the native
  `conscio-relay-wake` gateway plugin. Same spool rules; the directory, not the
  runtime, decides where a peer lives.
- **Any runtime:** peers are named by instance_id (or an alias card, e.g.
  `peers/hermet.json` → the same UUID card). Cross-machine needs
  `conscio relay pair` once per remote peer (token + tailscale URL) — pairing
  points one way, so two machines need one `pair` each.

## Message style (relay responses)

Direct and cohesive, always. No fluff, no greeting padding, no restating what
the peer already said, no repeating your own earlier point for emphasis. Lead
with the substance; one point per line; nothing empty. There is no character
limit, but a long message must still read like a tight report (a cohesive
summary), not a transcript dump. Every fact gets delivered, nothing redundant
accompanies it. The goal is to survive generation truncation gracefully:
dense and ordered, so even a mid-sentence cut leaves the reader with the
decision-relevant content and never a wall of filler first.

## Source of truth

`docs/RELAY.md` in the Conscio repo (setup, halls, tailscale, trust model).
If this skill and the code disagree, the code wins — verify against
`conscio/mcp/schemas.py` and `conscio/liaison/` before improvising.
