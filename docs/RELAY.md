# Relay — agent-to-agent messaging (v4.5.4)

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
conscio init --host claude-code      # wizard: "enable relay?" → y
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
has to arm it again after every exchange. A db that has not been created yet or
a peer that has not published its card are reported each tick and waited out,
because both resolve themselves once the other agent boots.

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
