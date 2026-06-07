# Semantic Reconciliation (v0.8)

> Theory: [Claude_Sentience](https://github.com/daveshap/Claude_Sentience) by
> Dave Shapiro — ontological coherence. v0.6 detected contradiction only by
> literal negation; v0.8 detects opposition by *meaning*.

## The mechanism

Embeddings give **similarity, not polarity**. v0.8 supplies polarity with
**antonym axes**: an axis (`availability: {+: operational, online…}{−: offline,
down…}`) defines two poles, each the centroid of its anchor terms' embeddings.
Two assertions contradict on an axis iff they project onto **opposite poles**,
each with cosine ≥ `AXIS_THRESHOLD` (0.62) and a ≥ `AXIS_MARGIN` (0.05) lead over
the other pole. A near-equidistant term is **neutral**, not contradictory —
this is what stops same-domain terms (`operational`/`degraded`) from reading as
opposites. Generalization is the payoff: an out-of-lexicon synonym like
`unresponsive` projects to the negative availability pole without being one of
its anchor terms (`offline`/`down`/`crashed`/`failed`/`unreachable`).

`AXIS_THRESHOLD` and `AXIS_MARGIN` are module constants in `conscio/semantic.py`,
calibrated for the spec; with real `nomic-embed-text` (768-D) they may want
tuning. They are left as constants for v0.8 — promote to env vars only if
production recall/precision demands it.

## Where it runs (the hot path stays cheap)

```
dream()  [OFF HOT PATH]
  Release → Prune → Reconcile (world.mark_contradictions(detector)) → Crystallize
                      └ writes cached `contradicted` flags into world_model.json

reflect() / coherence.assess()  [HOT PATH]
  ontological_score(world) reads world.contradicted_entities()  (cached, no network)
```

All embedding I/O is confined to dream's Reconcile and the opt-in output stage.
`coherence.assess` reads only cached flags. A cold world (never dreamed) reports
ontological 1.0 — no false dissonance before the first reconcile. Because the
dream's `coherence_before` is sampled before Reconcile, a non-ontological dream
can show a *negative* delta: it surfaces latent contradictions without pruning
them (ontological-dominant dreams prune the contradicted set and recover).

## Offline degradation

No Ollama → axes unavailable → `ContradictionDetector` falls back to the v0.6
lexical-negation rule (`coherence._relations_contradict`), fully preserved. The
detector is lexical-first, so even with Ollama up a literal negation is caught
without an embedding call.

## Output dedup (non-destructive)

`SemanticDedup` (opt-in, `CONSCIO_SEMANTIC_DEDUP=1`) **annotates** a near-
duplicate adjacent block (`↺ near-dup of above`) and keeps both verbatim. It
never merges or deletes — the deliberate answer to v0.6's rejection of merge-
based dedup (a false merge destroys content). Offline → no-op.

> **Caveat:** enabling `CONSCIO_SEMANTIC_DEDUP` adds the stage to the `reflect()`
> output pipeline, which then performs embedding calls (Ollama) on the hot path.
> It is therefore strictly opt-in and OFF by default; the default pipeline stays
> network-free.

## Custom axes are data, not code

Axis packs live in `conscio/presets/axes/*.json` (mirroring voice presets). A
domain team drops `legal.json` / `video.json` and selects it via
`CONSCIO_AXIS_PACKS=core,legal` — zero code edits.

## Tech debt resolved

`coherence.ontological_score` and `dreaming` no longer read the private
`world._data`. New public accessors — `WorldModel.list_relations()`,
`entity_count()`, `contradicted_entities()` — finish the cleanup the v0.6 doc
flagged.
