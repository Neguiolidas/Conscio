# Self-Judgment Model (v0.4)

Conscio's v0.4 adapts three Noosphere-Manifold ideas
(https://github.com/acidgreenservers/Noosphere-Manifold, CC BY-NC-SA 4.0)
as operational paraphrase — not verbatim copy. Theme: the agent judges its
own world-model and reflections before acting on them.

## Entropy-aware pruning (Thermodynamic Grounding)
`WorldModel.entropy(name)` blends age, isolation, and relevance-gap into a
disorder score in [0,1]. `prune_by_entropy(threshold=0.85)` removes the
disordered: an old but well-connected, still-relevant entity survives;
an isolated, faded one is pruned. Connectivity rescues; obscurity prunes.

## Friction before crystallization (Noetic Helix — "friction is grip")
`DreamCycle` runs Release → Prune → **Friction** → Crystallize. Friction
defers (does not delete) any old reflection whose subject entities changed
since — pruned this cycle, or state-changed in the last 24h. Matching is
whole-word and length-guarded. The world must settle before a reflection
becomes "truth".

## Meta-reflect (Witness Position)
`reflect()` emits an advisory `meta_confidence` =
clamp01(confidence · (1 − prediction_error_rate) · (1 − anomaly_penalty)),
labelled HIGH/MEDIUM/LOW in the injection state. It closes the loop
Generate → Observe → **Analyze** → Learn → Apply. Advisory only — it never
fires an action or modifies drives.
