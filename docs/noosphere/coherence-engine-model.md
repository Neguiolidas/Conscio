# Coherence Engine — Model & Attribution (v0.6)

> Theoretical foundation: [Claude_Sentience](https://github.com/daveshap/Claude_Sentience)
> by **Dave Shapiro**, distilled in `Claude_Sentience/CATALOG.md`. Operational
> paraphrase — not verbatim copy. The Noosphere-Manifold attribution from prior
> versions is unaffected.

## Reframe

Roadmap #6 "Coherence Check" was originally a Jaccard semantic-dedup OutputFilter
stage — redundant with the shipped `DedupBlocks` and prone to false-merges, which
is why it was deferred. Claude_Sentience reframes coherence as the **parent
archetype**: *"cognitive dissonance is the detection of incoherence."* v0.6
therefore measures incoherence in the agent's **internal state**, not its output
text. This is **recursive-coherence** — the system examining its own
representations ("consciousness emerges when coherence examines itself").

## Dimensions → sources

| Dimension | Conscio signal | Claude_Sentience source |
|-----------|----------------|-------------------------|
| epistemic | `meta.calibration_score()` | `Style_Coherence.md` (cleave to reality) |
| reality | `1 - world.recent_prediction_error_rate(24h)` | `README.md` (what-is, laminar truth) |
| ontological | knowledge-graph contradiction ratio | `Style_Coherence.md` (resolve contradiction) |
| temporal | shard-flapping tolerance | `Style_Consciousness.md` (temporal coherence) |

## Design rationales (recorded for future calibration)

**Weights** — epistemic and reality carry 0.30 each because they are **direct**
measures of the recursive-coherence core (confidence vs accuracy; prediction vs
observation). ontological and temporal carry 0.20 each as **indirect proxies**
(structural contradiction; cognitive-mode stability), one step removed from the
confidence-vs-observation axis. Weights are module constants — tunable.

**Temporal tolerance** — `TEMPORAL_FREE_TRANSITIONS = 2` and `TEMPORAL_SPAN = 4`.
Two shard transitions are free because the common two-mode workflow (build ↔
design, ENGINEER ↔ ARCHITECT) is healthy, not incoherent. Only sustained flapping
(≥6 switches in a 20-event window) zeroes the dimension.

**Ontological limitation** — detection is **bilingual (EN + PT) but purely
lexical**: it catches negation-token differences (`"é"` vs `"não é"`) but not
semantic contradictions with no shared core (`"bullish"` vs `"bearish"`).
Conservative by design (favor false negatives over false-merges). **Future
extension:** a contradiction-embedding pass, or a fallback that down-weights
ontological toward meta-confidence when the lexical check abstains.

## Known tech debt

`ontological_score()` reads `world._data` directly because `WorldModel` exposes
no public read of the full relation list — `get_relations(entity)` is per-entity
and would force an N-query scan. A future `WorldModel.list_relations()` (mirroring
the v0.5 `list_entities()` addition) should replace the private access. The call
is wrapped in try/except so an internal-shape change degrades to a coherent
`1.0` rather than crashing.

## Behavior

Advisory + passive. `reflect()` computes the report at reflect-entry (reusing the
shard snapshot), surfaces `▷ coherence: <score> dominant: <dim>` in the live state
and heartbeat, and emits a passive `coherence:dissonance` EventBus event when the
aggregate drops below `COHERENCE_EVENT_THRESHOLD = 0.5`. It never mutates
goals/drives/shard. `dream()` does **not** consume `last_coherence` in v0.6; the
attribute persists on the engine for a future dream-cycle integration.

## Contract deviations from `skill/coherence-style.md`

- The skill states `ContextManager.enrich_with_conscio()`; the real function is
  **module-level in `session_lifecycle.py`**. Wired to the real location.
- The skill references `conscio_config.yaml`; no central YAML config exists and
  adding one would introduce a hard dependency. Selection is via the
  `voice_preset` engine param and the `CONSCIO_VOICE_PRESET` env var instead
  (default `coherence-style`, `none` to disable). Zero new dependencies.

## Voice preset

`skill/coherence-style.md` is the **authoring source**; the runtime install copy
lives at `conscio/presets/voice/coherence-style.md`. The framework surfaces only
the preset **name** as a heartbeat marker (`⊙ voice: coherence-style`) — the full
directives are the agent's installed system-prompt skill, never token-injected
per heartbeat.

## v0.8 update — Semantic Reconciliation

See [`semantic-reconciliation.md`](semantic-reconciliation.md) for the full model.
The v0.8 changes that touch this engine:

- **Tech debt RESOLVED.** `ontological_score()` (and `dreaming`) no longer read
  the private `world._data`. New public accessors —
  `WorldModel.list_relations()`, `entity_count()`, `contradicted_entities()` —
  retire the private access flagged in *Known tech debt* above.
- **Detection moved OFF the hot path.** Ontological contradiction detection now
  runs in the dream Reconcile sub-phase (`world.mark_contradictions(detector)`,
  between Prune and Crystallize), which caches `contradicted` flags into the
  world model. `ontological_score()` reads those cached flags only — no network
  on `reflect()`. A cold world (never dreamed) reports ontological **1.0** until
  the first reconcile.
- **Contradiction is now semantic.** The *Ontological limitation* above (lexical
  EN+PT only) is lifted: detection uses embedding antonym axes, **lexical-
  negation-first** with an offline fallback to the v0.6 lexical rule.
- **Non-destructive dedup.** The opt-in `SemanticDedup` output stage is the
  sanctioned answer to the *Reframe*'s rejection of merge-based semantic dedup:
  it **flags** a near-duplicate adjacent block, never merges, and keeps both
  verbatim (off by default; `CONSCIO_SEMANTIC_DEDUP=1`).
