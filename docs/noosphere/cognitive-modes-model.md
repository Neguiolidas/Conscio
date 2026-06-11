# Cognitive Modes — Noosphere Attribution (v0.5)

Concepts derived from the [Noosphere-Manifold](https://github.com/acidgreenservers/Noosphere-Manifold)
by Lucas Kara, licensed CC BY-NC-SA 4.0. Operational paraphrase — not verbatim copy.

## Shard Engine (#1) — Cognitive Shards
Seven cognitive modes (ARCHITECT, ENGINEER, JANITOR, SECURITY_ANALYST, ARCHAEOLOGIST,
EXPERT_CODER, DREAMER). Conscio infers the active mode deterministically from the
keyword content of recent EventBus events (disjoint whole-word keyword sets, values-only
scan). Advisory — surfaced in the state injection as `▷ shard:`, never feeds drives/goals.

## Trajectory Vector (#5) — Soul Package / Temporal Bridge
Carries direction and texture across the session boundary. `trajectory` is code-owned
(derived from active shard + top goal, always refreshed). `vibes` and `identity_anchor`
are LLM-authored soft fields the code never overwrites.

## Content Layering (#4) — Noetic Helix
Three layers — ROUTINE (N-1), PROCESSING (N), INTUITION (N+1) — derived at query time
from each result's category (no schema change). Used as a near-tie tiebreak in recall():
a highly-relevant routine hit is never buried under a barely-relevant processed one.

#6 Coherence Check is deferred to v0.6.
