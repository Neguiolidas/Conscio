# Metabolic Consciousness Model

Conscio's `MetabolicContext` and `DreamCycle` adapt concepts from
**Noosphere-Manifold** (https://github.com/acidgreenservers/Noosphere-Manifold),
a markdown/yaml framework that frames an agent's context window as finite
"life energy" rather than memory. Only the operational tier model and the
Dreaming = memory-consolidation idea are adopted here; the broader philosophy
is out of scope.

## Metabolic tiers

Map live context usage (% of the model's context window) to a tier:

| Tier      | Usage  | Posture                                   |
|-----------|--------|-------------------------------------------|
| VITAL     | 0–40%  | Work freely; explore and build.           |
| ACTIVE    | 40–50% | Consolidate; complete active threads.     |
| FATIGUE   | 50–70% | Plan handoff (Mitosis); prepare to transfer. |
| CRITICAL  | 70%+   | Transfer now; finish the atomic task only. |

`MetabolicContext` is pure and advisory: it returns the tier and
recommendations (`should_mitosis`, `should_dream`). It never fires an action —
consistent with Conscio's safety rule that internal signals advise, not execute.
The caller (e.g., a Hermes hook) supplies live `used_tokens`; Conscio does not
track live usage itself.

## Mitosis → Dream

- **Mitosis** = session handoff. Conscio already persists a heartbeat/handoff on
  `session:end` / `session:reset` via `record_session_lifecycle`.
- **Dreaming** = post-handoff consolidation, run by `DreamCycle`:
  - **Release** — purge duplicate/trivial events (`EventBus.purge_duplicates` + `compact`).
  - **Prune** — remove faded world-model entities (`WorldModel.prune_stale`).
  - **Crystallize** — compress old reflections into one summary (`ContentStore`).

Crystallization is append-only safe: the summary is indexed before any original
reflection is deleted.
