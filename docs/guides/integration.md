# Consuming awake output

Running Conscio in [Awake Mode](safety-rules.md) only pays off if your host
agent reads what it concluded. v1.6 makes that a documented contract with two
surfaces — **pull** and **tail** — and a provenance gate that decides which
goals a host may auto-execute.

## The contract

> The host auto-executes **only** goals tagged `executable: true`. Goals tagged
> `executable: false` are *diagnostic* — surface them to the operator, never run
> them automatically.

This is what stops a context-compaction artifact (or a self-referential
introspection goal) from running without consent — the v1.6 `#7` gate.

## Pull: `engine.advisory()`

Call it each turn. It is cheap, **read-only**, makes **no inference call**, and
mutates nothing — safe to call on every host turn, even with no adapter attached.

```python
adv = engine.advisory()

for g in adv["goals"]:
    if g["executable"]:
        inject_into_context(g["description"])   # host may act on these
    else:
        notify_operator(g["description"], origin=g["origin"])  # show, don't run

if adv["status"]["action_lockdown"]:
    pause_autonomy()
if adv["status"]["brake"]:
    alert(adv["status"]["brake"])               # failure-rate brake tripped (#8)
```

### Shape

| Key | Meaning |
|-----|---------|
| `awake` | Awake Mode gate (R9) — autonomy allowed only when `true`. |
| `reflection` | Latest inner-monologue insight. |
| `meta` | Confidence / self-assessment summary. |
| `goals` | List of `{description, origin, executable}`. |
| `coherence` | `{score, dominant}` — aggregate coherence + dominant dissonance. |
| `status` | `{action_lockdown, dream_recommended, brake}`. |
| `recommendations` | Derived hints, e.g. "N diagnostic goal(s) pending review". |

### Goal origins

`origin` is the goal's provenance. Executable origins are externally or
environmentally grounded; diagnostic origins are self-referential / error /
compaction-derived.

| Origin | Executable? |
|--------|:-:|
| `user`, `internal`, `curiosity`, `anomaly`, `maintenance` | ✓ |
| `meta_error`, `self_prompt`, `compaction` | ✗ (diagnostic) |

If your host derives a task from a compaction artifact, tag its provenance so the
gate can do its job:

```python
engine.goals.add_user_goal(text, origin=GoalOrigin.COMPACTION)  # -> diagnostic
```

## Tail: `daemon_heartbeat.json`

When the daemon runs out-of-process, you don't need to import Conscio — every
cycle it writes `<storage>/daemon_heartbeat.json`:

```json
{
  "ts": 1781740000.0, "cycles": 12, "awake": true, "pid": 4242,
  "last_run": {"cycles": 3, "failures": 0, "stopped": "max_cycles"},
  "advisory": { "...": "same shape as engine.advisory()" }
}
```

`tail -f` it, or poll on your own cadence. The `last_run` block is the previous
heartbeat's [`RunReport`](../reference/public-api.md) summary; `advisory` is the
full snapshot above.

## Full example

See [`examples/host_consumer.py`](https://github.com/Neguiolidas/Conscio/blob/main/examples/host_consumer.py)
for a runnable, offline end-to-end host that pulls the advisory, splits
executable from diagnostic goals, and prints the operator-facing decision.
