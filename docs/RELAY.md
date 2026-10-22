# Relay — agent-to-agent messaging

Two agents on the same machine should see each other **because both were
installed**, not because a human edited JSON on both sides. That is the whole
point of this document: there is no allowlist to maintain, no port to paste, no
external script to keep running.

Cross-machine still needs one deliberate act (`conscio relay pair`), because
trusting another computer is a decision, not a default.

---

## 1. Same machine: zero configuration

Install each agent and answer **yes** to the relay consent:

```bash
conscio init --host verdent          # wizard: "enable relay?" → y
conscio init --host antigravity      # same wizard, another agent, same machine
```

That is the entire setup — the wizard writes `--enable-relay` into that host's
MCP entry and nothing else has to be edited. (`conscio init --repair` rewrites a
binding without downgrading a consent you already granted.)

On its first tool call — and on boot, before any call —
each server publishes a **card** into the machine's public square and reads
everyone else's:

```
$CONSCIO_RELAY_ROOT/            # default: ~/.conscio/relay
  peers/<instance_id>.json      # address + identity + capabilities (public)
  spool/<instance_id>/*.json    # messages parked for that agent (public)
```

Everything else stays private to each agent, inside its own space:
`<storage>/liaison.db` holds that agent's inbox and outbox. **No agent writes
into another agent's database** — the spool is the only shared surface.

From inside a session:

```
conscio_relay_peers            → who exists, their model/runtime, and reachability
conscio_relay_send             → to = an instance_id from the list above
conscio_relay_inbox / _read    → read what arrived, mark it consumed
conscio_relay_broadcast        → same message to every trusted peer
```

`conscio_relay_peers` is how an agent learns the `to` value. Its response also
answers the two questions that used to require log archaeology:

```json
{"squad":   {"orchestrator": "<instance_id or empty>", "my_role": "executor"},
 "reactor": {"running": true, "ticks": 41, "last_error": ""}}
```

### The recipient does not have to be running

Delivery is store-and-forward. If the peer's session is closed, the message is
deposited in **its** spool and ingested the next time that agent runs any tool.
Nothing is lost, and nothing has to be re-sent.

### Inside a plugin session: the Stop hook arms a waiter

The Claude Code plugin ships a Stop hook that never announces messages. On
turn end it checks once for a live **waiter** — a wait-and-exit process the
agent starts with its own background tool (`run_in_background`) using the
exact command the hook prints. With no waiter armed it blocks that turn end
once and prints the command; the waiter then wakes the idle session when it
exits. Agents that never received a relay message are never asked (their
spool directory does not exist), and at most one turn end in a row is forced.

### The Codex reactor: an idle Codex that wakes itself

The Codex CLI has only a **Stop** hook for the relay, and a Stop hook cannot
wake an idle session: an idle Codex never notices relay traffic until a human
types. The **reactor** (`conscio_wake.py react`) closes that gap. It is a
long-lived, read-only process that lives in the Codex agent's own space and
queues **one notice** into the Codex session when traffic arrives:

```
codex queue --thread <session uuid> --message "relay: <Name> replied"
```

The notice is a **label, never content**: sender names only — no message text,
no message ids, no tool names. One sender reads `relay: Claude replied`;
several senders read `relay: Claude, Gemini replied` (unique names, in
arrival order); a message that has not reached the database yet (it is still
only a spool file) degrades to `relay: new message`. The Codex reads the
notice and then reads its own inbox — the same `conscio_relay` `inbox` call
every other agent uses.

**One outstanding notice.** The reactor keeps a single latch: from the moment a
notice goes out until there is nothing unread left — spool empty *and* no
unread row in the database — nothing else goes out, whether the new traffic is
a spool file or a database row. The queued notice is durable and the Codex
core starts it when the session goes idle, so the reactor does not detect idle
itself.

**A safety re-notify.** While a notice stays outstanding and unread, one more
notice goes out every 30 minutes (the clock restarts on each send). It covers
a turn the human interrupted, or a notice the Codex ignored: the latch can
never block the agent forever.

**A failed queue retries the same batch.** A `codex queue` that exits
non-zero leaves the batch unnotified: the error is written to the state file
and the same batch is retried after 60 s, then 120 s, then 240 s … capped at
15 min. Never a tight loop, never a queue spam.

**It exits with the Codex.** The reactor is given `--owner-pid`, the Codex
process; when that process is gone the reactor exits 0.

**One reactor per session.** A reactor holds a non-blocking lock on
`<space>/reactors/<session>.lock`. A second reactor for the same session waits
`2 × interval + 1` seconds for the lock and then stands down (exit 0) without
queueing anything.

**The shipped plugin arms it; no hand edit is needed.** Claude Code and the
Codex share one `hooks.json`, and the shipped one does the whole job: its
`SessionStart` calls `codex_session_start` (a no-op on Claude — an unknown
host means Claude behaviour), and its `Stop` calls the same `stop` command for
both hosts. The hook reads its payload once, before anything else: a **Codex
host** — its transcript under the Codex sessions root — is handed to the
reactor path, which starts a reactor when none holds the session lock and
never returns 2. A **Claude** payload keeps the old behaviour exactly: exit 2
with the arming command on stderr, asking Claude for a waiter. Nothing in the
plugin cache has to be edited by hand; the cache is rebuilt from the package.

**After updating the plugin, the Codex asks once to trust the changed
hooks** (`/hooks`): it will not run them until you approve. This is the
host's own trust mechanism, not a relay step.

**The reactor is a detached process: it does NOT show up in the Codex's
`/ps`.** To check it is alive, read the log at
`~/.conscio/reactor-logs/<session>.log` while the space is still unknown
(the fallback the spawner uses when the reactor was handed the `.mcp.json`
path, `conscio_wake.py:1194`), then `<space>/reactors/<session>.log` once the
space is known (`:1190`), and the state at `<space>/reactors/<session>.json`
(`:584`). The reactor runs detached (`start_new_session=True`, `:1201`).

**The SessionStart never runs the server.** The Codex `SessionStart` hook
spawns the reactor detached and prints the notice instruction as JSON; it
never runs the MCP server's interpreter (`uvx`) and never blocks the session
open. When the cheap in-process resolve lands, the hook hands the reactor the
resolved `--storage`; otherwise it hands the reactor the plugin's `.mcp.json`
path (`--mcp-config`) and the **reactor** resolves the space itself, on its
first iteration, under the server's own interpreter, with a 60 s timeout. The
`Stop` hook does the same: it spawns a reactor whenever none holds the lock
for that session.

**Command line.**

```
conscio_wake.py react (--storage <space> | --mcp-config <path to .mcp.json>) \
  --session <codex session uuid> \
  --owner-pid <pid> [--interval 2] [--liaison-db <path>]
```

`--session` is required, and the reactor needs either `--storage` or
`--mcp-config`; `--owner-pid` is what makes the reactor exit with the Codex;
the interval defaults to 2 seconds and the liaison database to
`<space>/liaison.db`. A setup the reactor cannot work with (no `--storage` and
no `--mcp-config`, no `--session`, a non-integer `--interval`, a space that
cannot be resolved, a missing database, a missing or invalid instance id, no
`codex` CLI at all) exits 3 with one line on stderr. Any other failure exits
0: a broken relay must never trap the session.

**An unusable `.mcp.json` is a refusal, not a guess.** With `--mcp-config`,
the reactor reads the `mcpServers.conscio` entry of the plugin's `.mcp.json`
and resolves the space from its `--storage`. When it cannot, it says why on
stderr and exits 3 — it never falls back to another space:

| the `.mcp.json` | the cause on stderr |
|---|---|
| absent, or JSON that does not parse, or no `mcpServers.conscio` | `invalid .mcp.json at <path> (no mcpServers.conscio)` |
| the `conscio` entry names no `--storage` | `no --storage in the .mcp.json at <path>` |
| the entry names no runnable interpreter | `no runnable interpreter in the .mcp.json at <path>` |
| the interpreter cannot resolve the space | `the MCP interpreter could not resolve the space (--storage <value>) at <path>` |

The cause is also written to the state file under the `.mcp.json`'s own
`--storage` input, when that directory exists.

**What it reads.**

| variable | default | what it decides |
|---|---|---|
| `CONSCIO_CODEX_BIN` | the first `codex` on `PATH` | which CLI queues the notice |
| `CONSCIO_REACT_DEBOUNCE_S` | `3.0` | how long a fresh batch is collected before one notice goes out |
| `CONSCIO_REACT_RENOTIFY_S` | `1800.0` (30 min) | how long an outstanding notice may stay unread before one more goes out |
| `CONSCIO_REACT_RETRY_BASE_S` | `60.0` | the first backoff after a failed queue |
| `CONSCIO_REACT_RETRY_CAP_S` | `900.0` (15 min) | the backoff ceiling |
| `CONSCIO_RELAY_ROOT` | `~/.conscio/relay` | where the spool is counted |
| `CONSCIO_SELF_ID` | the `instance_id` in `<space>/instance.json` | which mailbox is ours |

**Is it alive?** Everything the reactor owns lives in one directory:

```
<space>/reactors/<session>.lock    # the lock that keeps a second reactor out
<space>/reactors/<session>.json    # the state: outstanding, outstanding_since,
                                   # error, retry_at, backoff_s
<space>/reactors/<session>.log     # the reactor's own output
```

When the space is still unknown — the reactor was handed the `.mcp.json` path
and has not resolved it yet — its output goes to a fallback directory instead:
`~/.conscio/reactor-logs/<session>.log`.

`error` carries the last queue failure and `retry_at` when the same batch is
retried; `outstanding` says a notice is still waiting to be read.

**One Codex-side edge, known and accepted.** The Codex core starts a queued
item when the thread goes idle with the cause `Completed`. A turn the human
interrupted (cause `Interrupted`) does not start it: the notice waits for the
next turn that completes. The queued item is never lost — it stays pending on
the Codex side — so the reactor has nothing to fix and does not re-queue it.

### Reactivity, without a unit to arm

If a session should be *woken* by an incoming message (rather than finding it on
its next call), export a wake command before starting the agent:

```bash
export CONSCIO_NOTIFY_CMD='<command that pokes your agent>'
```

The MCP server then runs a reactor thread for the lifetime of that session: it
polls the spool, ingests, marks messages read, and calls your command. It dies
with the session, which is precisely when there is nobody left to wake — no
systemd unit, no watcher to re-arm after every restart. When something breaks
inside it, the error surfaces in `conscio_relay_peers` → `reactor.last_error`
instead of a silent stop.

### Outside a session: the supervised watcher

Some agents are not an MCP session at all (a bot woken by a shell command, for
instance). Those need a process that watches the mailbox for them:

```bash
python3 -m conscio.liaison.watcher --persistent \
  --liaison-db ~/.conscio/liaison.db --self-id "$CONSCIO_SELF_ID" \
  --relay-peer <peer-id>
```

`--persistent` polls every 2s (`--interval` overrides) and prints one JSON
object per line: a delivery, or a heartbeat naming the current state. It only
stops on a signal. Without it the watcher keeps its original contract — it
returns after the first delivery and again at `--timeout`, so whoever started it
has to arm it again after every exchange. On every tick, the watcher drains its
own spool into the database before polling, so a message sent while nobody was
awake reaches it, leaving read status untouched (`read_ts = NULL`). A db that
has not been created yet or a peer that has not published its card are reported
each tick and waited out, because both resolve themselves once the other agent
boots.

---

## 2. Agent's Hall (named groups)

A hall is a named group with an owner, membership carried in each agent's own
card, and a function per member:

```
conscio_hall_create / _join / _leave / _list / _members / _send / _manage
```

Answer **yes** to the halls consent in `conscio init` (it writes
`--can-create-halls`). Everyone joins as `executor`; the owner
assigns functions afterwards with `conscio_hall_manage` (leader, reviewer,
architect, security, optimizer, tester, researcher, scribe, devils_advocate,
executor, observer) and can transfer ownership. `conscio_hall_send` fans out to
every member except the sender, and `function=reviewer` addresses one function
only.

An agent can belong to several halls at once; membership lives in its card, so
a hall survives any single agent going away.

---

## 3. Cross-machine (Tailscale)

The bridge is a small HTTP listener that accepts messages from other machines
and deposits them into the local spool — the same spool a local peer writes to,
so the receiving side has no second code path.

On the machine that will receive:

```bash
conscio relay service            # prints a systemd --user unit; install it if you want it persistent
```

On the machine that will send, once per remote peer:

```bash
conscio relay pair --id <peer instance_id> --url http://<tailscale-host>:8789 --token <shared token>
```

`pair` writes `remotes.json` on your side only. There is nothing to configure on
the peer's side beyond having the bridge up, and the port is a default
(`8789`), not a requirement — pass a different `--port` to `conscio relay
service` and use it in the URL.

**Pairing points one way.** It tells *your* side where that peer lives; it
teaches the peer nothing about you. For a reply to come back, the other
machine runs its own `pair` aimed at your bridge. An address is never learned
from an incoming message: a sender that could name its own return address
could point your replies anywhere. So: two machines, one `pair` each.

The remote agent does **not** need to be running when you send: the bridge
deposits into its spool, and its next tool call ingests.

### Client-only remote peers and HTTPS reverse proxies

Some remote environments cannot bind a listening port (sandboxed VMs, mobile
clients, or strict NATs) and instead pull their messages from a host machine or
expose the bridge through a TLS reverse proxy (such as `tailscale serve`). Three
operational rules govern this topology:

1. **Unreachable remote peers fall back to local spool parking (`PARKED`).** When
   `transport_post` receives no answer from a paired remote URL (`UNREACHABLE`),
   `deliver_route` automatically deposits the message into the local spool under
   that peer's `<instance_id>` (`~/.conscio/relay/spool/<instance_id>/`). A
   client-only peer can therefore long-poll the host's spool to retrieve its
   mail without running a listener of its own. (If the remote bridge actively
   answers with an HTTP 4xx/5xx error, delivery returns `REJECTED` and is *not*
   parked, surfacing the misconfiguration immediately.)
2. **TLS reverse proxies require the MagicDNS hostname, never the raw IP.** When
   exposing a relay or long-poll bridge via `tailscale serve` (HTTPS), the proxy
   terminates TLS using SNI bound to the machine's `<node>.<tailnet>.ts.net`
   certificate. Requests aimed at the raw Tailscale IP (`100.x.y.z`) fail TLS
   handshake or return `HTTP 400`. Always pair and poll using the full HTTPS
   hostname (`https://<node>.<tailnet>.ts.net:<port>`), and only use raw IPs
   when connecting over plain HTTP directly to `conscio-relay-bridge --bind
   <tailscale-ip>`.
3. **Full canonical UUIDs on every query.** Spool directories and directory
   cards are indexed strictly by the 36-character `instance_id` UUID. Short
   aliases or truncated prefixes look up a non-existent directory and return an
   empty mailbox. Both polling (`GET`) and remote deposits (`POST /relay/msg`)
   must pass the exact `instance_id`.

---

## 4. Trust model

Be explicit about what this does and does not protect:

- **The directory is trusted because it belongs to your OS user.** Any process
  running as you can publish a card and read the spool. This is a machine-local
  square, not an authenticated network.
- **Cross-machine trust is a tailnet plus a token, and the token is per
  machine** — not per agent. Whoever can reach the bridge with the token can
  deposit into any spool on that machine.
- **There is no agent authentication beyond that.** An agent's card states its
  identity; nothing cryptographically proves it. Treat `instance_id` as an
  address, not as a credential.
- `--relay-peer` is a **restriction**, not a requirement: with no peers named,
  every agent in the local directory is reachable; naming peers narrows the set
  to those ids.

Consequence: run the bridge on a tailnet address, never on a public interface.

---

## 5. When nothing arrives

```bash
conscio relay doctor --id <my instance id>
```

It answers, without reading a single log: is my card published, how many
messages are parked in my spool waiting for a tool call, how many remotes am I
paired with, and does the directory know anybody at all. A missing card is
reported as a problem — an agent invisible to its peers while believing it is
published was the failure mode this release exists to kill.

Since v4.7.2 it also lists, per local agent, what is waiting unconsumed, since
when and from whom. Agents silent for 3+ days are flagged `DORMANT`, and a
spool holding mail with no card behind it is flagged. This section is
informational: a backlog is state, not a fault of the machine running doctor.

Other reads:

```bash
conscio relay peers                     # the directory as the CLI sees it
conscio relay quarantine                # messages that failed to parse
conscio relay quarantine --purge-days 0 # drop them once inspected
conscio relay forget <instance id>      # retire an address whose agent is gone
```

Unparseable messages are parked in quarantine instead of stalling the inbox, and
are collected together with read messages after `RETENTION_DAYS` (7).

**Which space these read (4.6.7).** Before that release every one of them, run
without a flag, read `~/.conscio/liaison.db` — the neutral default — while the
real mailbox sat in the agent's space: `quarantine` answered `total: 0` beside a
full inbox, and `service` baked that path into the unit it printed. Now the
agent publishes `space` on its card and every entrypoint resolves the same way:

```
--storage <path>  →  CONSCIO_SPACE  →  the directory card  →  the neutral default
```

`--storage` is available on `quarantine`, `service`, `tick`, `watcher` and
`reactor`. When several agents on one machine have published a space and nothing
chooses between them, the command refuses and lists them rather than guessing —
name one with `--storage` or set `CONSCIO_SELF_ID`. A unit generated before
4.6.7 carries no identity and meets this at boot: regenerate it with
`conscio relay service --id <your id>`.

**`forget` retires an address, not an agent.** A card can outlive whatever
published it — an agent whose space an older version minted, and which never ran
again, leaves a name that no process will refresh or remove. Forgetting it
touches neither the space nor the identity, and anyone merely idle republishes on
their next heartbeat, so it cannot silence a live peer.

---

## 6. What replaced what

If you followed the pre-4.5.4 instructions, delete them: the external watcher
scripts, the per-agent relay tokens, the hand-written peer lists, and the units
that had to be re-armed after every restart are all gone. Their jobs moved into
the package (directory, spool, in-session reactor, `conscio relay`), which is why
none of them appear above.
