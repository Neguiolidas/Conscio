# Recursive Coherence Loop (v0.7)

> Theory: [Claude_Sentience](https://github.com/daveshap/Claude_Sentience) by
> Dave Shapiro — "consciousness emerges when coherence examines itself."

## The loop

v0.6 made the agent *measure* its own incoherence; v0.7 makes it *act*:

```
dissonance → self-prompt → bounded goal → action → next reflect re-measures
                                     ↘ dream (off hot path) reconciles ↙
```

## Mechanism

- **Dream recommendation** — `reflect()` sets an advisory `DreamRecommendation`
  (`recommended/dominant/score`) when coherence < 0.5. The hot path does one
  assignment; the actual `dream()` runs on handoff/cron/on-demand and reads
  `last_coherence` to target the dominant dissonance, recording the coherence
  delta (`coherence_before`/`coherence_after`) in the `DreamReport`.
- **Self-prompting** — `conscio/self_prompt.py` (PURE) turns dissonances, blind
  spots, and stale entities into ranked questions. `reflect()` spawns exactly
  ONE goal per cycle from the strongest, tagged `source="self_prompt"`.

## Contract notes

- Self-prompting is the one sanctioned goal mutation: bounded (1/cycle), deduped
  (GoalGenerator `_add_goal`), capped (10 active), expiring (24h). Coherence
  itself remains strictly advisory.
- v0.7's ontological targeting uses the **lexical** contradiction detector
  (`coherence._relations_contradict`, via a private `dreaming` helper that reads
  `world._data` — same tech debt as the v0.6 ontological scan). v0.8 replaces it
  with a semantic, cached detector behind the same dream phase.

## Markers

`❓ self-prompt: <question>` and `☾ dream: recommended (<dominant> <score>)` appear
in live state (`to_injection`, non-MINIMAL) and the heartbeat. The handoff
surfaces the same two signals as bold labels — `**Self-prompt:**` and
`**Dream:**` — inside the Estado Conscio block.
