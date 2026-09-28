# Ambient — per-machine task board (v4.8)

Ambient gives each machine **one** task board that the relay reactor sweeps on
its own tick. An agent that has work assigned to it on the board is *noticed*
over the relay first; a live (or absent) agent may then be *woken* by the
machine's connector. The whole thing is **off by default** and is storage plus
CLI/MCP — no new daemon.

Everything lives under the machine's relay root:

```
<relay_root>/ambient/
├── board.db            # the board (SQLite, WAL, user_version=1, busy_timeout=5000)
├── enabled            # the flag: present = ambient is on, absent = off (default)
├── agents.json        # the wake registry (who this machine may wake)
└── board.db.sweep.lock
```

One board per relay root (invariant I1): the node, the CLI and the MCP tool all
resolve the same `board.db`, so there is never more than one source of truth
for a machine's tasks.

## Turn it on

```
conscio ambient enable          # create <relay_root>/ambient/enabled
conscio ambient disable         # remove it (the board and CLI keep working)
```

While the flag is absent the reactor's ambient node gives its sweep back and
touches nothing; the board and the CLI remain fully usable. Enable is what
switches the node from *board + CLI only* to *board + relay notice + wake*.

## The wake registry (`agents.json`)

The registry maps an instance id to a connector and a budget. **Every agent's
`wake_budget_per_day` defaults to 0** — nobody is woken until you opt an agent
in, per machine:

```json
{
  "9f2c…": {"connector": "claude-bg", "model": "sonnet",
            "wake_budget_per_day": 4, "cwd": "/srv/app"}
}
```

`model` is passed to `claude --bg --model` as is; omit it to use Claude Code's
default. Set `cwd` to a trusted project directory. Without it the wake runs in
the home directory, and `claude --bg` refuses an untrusted workspace ("The home
directory is trusted one session at a time"), which is recorded as
`spawn_error`.

An absent registry means *nobody is woken* (not an error). A malformed one is
reported by `conscio ambient doctor` as `registry: agents.json …`, and the gate
refuses to wake with `no_connector` until it is fixed.

## The loop: `create → claim → submit → review`

The **orchestrator** is the one role that may *create* backlog work. It holds a
short orchestration lease; execution is fenced to it.

```
# 1. take the dispatch lease (the orchestrator; renew before it lapses)
conscio ambient orchestrate acquire
conscio ambient orchestrate renew        # later, before the lease expires

# 2. create a task with an assignee (and optionally a reviewer)
conscio ambient task create --title "fix the flaky test" --body "…" \
    --assignee A --reviewer R

# 3. the assignee CLAIMS it (files are reserved atomically with the claim)
conscio ambient task claim <id>

# 4. … work happens … then SUBMIT (back to the reviewer, same fence)
conscio ambient task submit <id>

# 5. the reviewer renders a verdict (approve → done, reject → back to the executor)
conscio ambient task review <id> --verdict approve
```

Fences are per-task and per-orchestration version numbers: a write that carries
a stale fence is refused (`StaleFence`), so a lapped holder or a superseded
submission can never clobber the live state. A rejected task returns to its
executor **on the fence the reviewer was notified on** — the executor is still
told about it.

## `doctor` and `report`

```
conscio ambient doctor            # flag, board, sweeper, admission, wake residue,
                                  # reactor units, claude --version
conscio ambient doctor --prune    # also drop events / done-cancelled tasks > 90 days

conscio ambient report            # board events counted by kind
conscio ambient report --since 8h # window: 30m | 8h | 2d
```

`doctor` is the read-only self-check you run after re-arming a reactor; it names
what is present, what is missing, and why the gate would (or would not) wake an
agent. `report` is the plain count of `board_events` by kind over a window —
useful for the refused-wake and stalled-review signals.

## Waking

The gate order is `connector → budget → admission → liveness → concurrency`;
the first "no" is the answer and is recorded once per (task, fence) so a
stuck reason is visible without event spam. `conscio ambient wake <id> --dry-run`
runs the whole gate and records a `wake_dry_run` event — it **never** spawns.
In v4.8 only the node spawns, never the CLI.

### The `claude-bg` connector

The one connector in v4.8. A wake runs `claude --bg [--model M] <prompt>` inside
its own `systemd-run --user --scope`, so the session survives a reactor
restart. The environment is built from scratch (`PATH`, `HOME`, `LANG`,
`XDG_RUNTIME_DIR`, plus the Conscio identity variables); the prompt is a fixed
template carrying only the task id, passed as a single argument. Output goes to
temporary files, stdin is `/dev/null`, and the call times out after 60 s.

| `claude --bg` outcome | recorded as |
|---|---|
| exits 0 and prints `backgrounded · <id>` | a running session with that id |
| fails and mentions `429` or a rate limit | `rate_limited` |
| any other failure, timeout, or no id | `spawn_error` / `timeout` |

Liveness is read from `claude agents --json`:

| session in the listing | liveness |
|---|---|
| `working` | live — the gate refuses a second wake |
| `done`, `failed`, or absent from the listing | not live |
| `blocked`, an unrecognised state | unknown |
| the command fails or prints something that is not the known JSON | unknown |

**Unknown never wakes** — the agent may still be running. A failed
`claude stop` is logged, and the session is still recorded as stopped on the
board.

### Provisional constants

Seven constants in `conscio/ambient/node.py` are provisional until they are
calibrated, and changing them is the owner's call: the admission gate's
`WAKE_FLOOR_MB`, `LOAD1_DELTA_TOLERANCE`, `ADMISSION_WINDOW`, `ADMISSION_MAX_AGE_S`
(spec §7.3); `WAKE_GRACE_S`, `RENOTIFY_MAX` (spec §7.4); and
`MAX_CONCURRENT_WAKES=1`. A single measured wake peaked at 917 MB RSS for the
whole process tree, under the 1500 MB floor; two simultaneous wakes were never
measured, so concurrency stays at one.

### Proposals over the relay

`board.propose` is the one board write that travels over the relay:
a remote peer asks for work by sending `board.propose`, which becomes a
`proposed` task with `creator = sender` and `origin = <message id>` (a redelivery
dedupes on `origin`).
