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
| `structural` | `{loaded, commit, hash, nodes, hyperedges, communities}` or `null` — the loaded code graph; see [Structural cognition](#structural-cognition). |
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

## Structural cognition

Conscio can give the refined model **structural awareness of the codebase it is
working in**, distilled from a [Graphify](https://github.com/)-format
`graph.json`. The graph is consumed as **data, never code** (R10): parsed with
`json` only, no `networkx`, no Graphify runtime dependency. It is **opt-in** —
nothing is injected until you load a graph.

```python
sig = engine.load_structure("graphify-out/graph.json")   # distils once
# from here, get_state_for_injection() appends a budget-adaptive structure block
```

The distilled signal is the graph's **curated hyperedges + per-community
summaries**, not its thousands of raw nodes. Injection is sized to the model's
context window (scales from ~120 tokens at small contexts up to ~1200), and
renders **labels only** — never raw node-ids — so it stays compact and safe.

Drill down on demand with the pull surface (an `advisory()` sibling — read-only,
no inference):

```python
engine.structural_lookup("conscio_engine_reflect")
# -> {"kind": "node", "label": "ConsciousnessEngine.reflect",
#     "source_file": "conscio/engine.py", "source_location": "187", ...}
engine.structural_lookup("agency_act_cycle_pipeline")   # -> {"kind": "hyperedge", ...}
engine.structural_lookup("0")                            # -> {"kind": "community", ...}
engine.structural_lookup("unknown")                      # -> None (always graceful)
```

**Staleness is yours to detect.** The signal carries `built_at_commit` and a
`content_hash` (surfaced in `advisory()["structural"]`); compare `commit` to your
current `HEAD` to know when to regenerate the graph. Conscio never runs Graphify
itself.

### Consent (workspace-scoped)

Ingestion is **consent-gated and defaults OFF** — nothing is read until an
operator grants consent for a workspace. Consent is per-`Workspace.id`, persisted
under `<storage>/structural_consent.json`:

```bash
conscio consent project   # ingest THIS workspace's graphify-out/graph.json
conscio consent parent    # ingest the PARENT multi-project folder's graph
conscio consent off       # revoke
conscio consent           # show the current workspace's scope
```

When run under the daemon, this is **switch-safe**: an agent that changes
workspace mid-run only ingests a workspace it has consented to, and any
previously loaded graph is **unloaded on switch-away** — one project's structure
never leaks into another's context. Reading the parent folder happens only with
explicit `parent` consent.

In-process hosts can drive the same policy directly:

```python
from conscio import StructuralConsent, ConsentScope, sync_structure
from conscio.structural_consent import consent_path

consent = StructuralConsent(consent_path(engine.storage))
consent.grant(workspace.id, ConsentScope.PROJECT)
sync_structure(engine, workspace, consent)   # auto-loads, or unloads if revoked
```

## Full example

See [`examples/host_consumer.py`](https://github.com/Neguiolidas/Conscio/blob/main/examples/host_consumer.py)
for a runnable, offline end-to-end host that pulls the advisory, splits
executable from diagnostic goals, and prints the operator-facing decision.
