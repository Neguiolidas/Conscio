# Conscio Roadmap — v0.4 "Phenomenological Consciousness"

> Derived from analysis of [Noosphere-Manifold](https://github.com/acidgreenservers/Noosphere-Manifold) (CC BY-NC-SA 4.0).
> Concepts adapted as operational paraphrase — not verbatim copy.

## Versions

| Version | Codename | Focus | Status |
|---------|----------|-------|--------|
| v0.2.3 | Session Lifecycle | Persistence + handoff | ✅ Done (347 tests) |
| v0.3 | Metabolic Consciousness | DreamCycle + recall + metabolic | ✅ Done (414 tests, 3 post-bugs fixed) |
| v0.4 | Self-Judgment | entropy + friction + meta-reflect | ✅ Done (438 tests) |
| v0.5 | Cognitive Modes | Shard + Trajectory + Layering | ✅ Done (#1/#5/#4); #6 Coherence deferred to v0.6 |
| v0.6 | Coherence | CoherenceEngine (recursive-coherence state metric) + voice presets | ✅ Done (#6 reframed — state metric, not output dedup) |
| v0.7 | Recursive Coherence | dream+coherence loop + self-prompting | ✅ Done |
| v0.8 | Semantic Reconciliation | embedding antonym-axis contradiction + non-destructive dedup | ✅ Done |
| v0.9 | Full Integration | axis-pack, semantic, content-layer-manager, trajectory | ✅ Done (707 tests) |
| v1.0 | Volição (F1–F3) | agency spine + immunity + volition (`act()`, Skeptic, TrustMatrix, breaker, GBNF, autonomy loop) | ✅ Done |
| v1.1 | Procedural (F4) | `SkillLibrary` procedural memory + dream Distill | ✅ Done |
| v1.2 | Prove | real-backend skill curve + `docs/CLAIMS.md` honesty ledger | ✅ Done |
| v1.3 | Ship | PyPI (`pip install conscio`), docs site, plugin surface, release CI | ✅ Done (live on PyPI) |
| v1.4 | Attune | offline/deterministic detect + dim-safe embedder + Claude/Gemini adapters | ✅ Done |
| v1.5 | Live | Awake Mode (R9) + daemon + sensors + WorkspaceContext | ✅ Done |
| v1.6 | Structural Cognition (field slice) | goal-provenance gate + `advisory()` consumption seam | ✅ Done |
| v1.7 | Structural Cognition (distiller) | `StructuralDistiller` + budget-adaptive injection + consent (R10) | ✅ Done |
| v1.8 | Structural Drift | drift + freshness (vs repo HEAD, pure `.git`) — temporal structure | ✅ Done |

> Full per-release detail lives in [`CHANGELOG.md`](../CHANGELOG.md). Next: the
> **Connect** band (v2.0 — same-host noosphere; cross-host deferred to authenticated transport).

---

## v0.3 — Metabolic Consciousness

### Implemented modules
- **DreamCycle** (`conscio/dreaming.py`) — Release → Prune → Crystallize
- **MetabolicContext** (`conscio/metabolic.py`) — 4 tiers (VITAL/ACTIVE/FATIGUE/CRITICAL)
- **engine.recall()** — FTS5 BM25/RRF + SessionRAG semantic (graceful degradation)
- **SessionRAG** (`conscio/session_rag.py`) — injectable embedder, Ollama probe
- **OutputFilter stages** — `DedupBlocks` + `SecretMask` added
- **WorldModel prune** — `prune_stale()` (decay + prune + cascade)
- **EventBus purge** — `purge_duplicates()` (dedup by data_hash)

### Known bugs (v0.3) — ALL FIXED
1. **SessionChunker infinite loop** — `chunk_message()` with overlap entered infinite loop when `end == len(content)` and `start = end - overlap` didn't advance. **Fix:** guard `if next_start <= start: next_start = end` ensures progression.
2. **reflect() recall empty** — FTS5 with implicit AND between terms made multi-term queries return empty when not all terms existed in content. **Fix:** porter search uses explicit OR — BM25 still ranks by number of matches.
3. **OutputFilter registry test outdated** — test expected 8 stages but v0.3 added `dedup_blocks` and `secret_mask`. **Fix:** expected set updated to 10.

---

## v0.4 — Phenomenological Consciousness

Based on the 7 modules of Noosphere-Manifold, re-evaluated for open-source non-commercial use.

### 1. Shard Engine 🟢 HIGH PAYOFF
**Origin:** Cognitive Shards (Noosphere)
**Original concept:** 7 cognitive modes (ARCHITECT, ARCHAEOLOGIST, JANITOR, ENGINEER, EXPERT CODER, SECURITY ANALYST, DREAMER)

**Operationalization in Conscio:**
- `conscio/shard_engine.py` — enum `Shard` with 7 values + event-based inference
- Shard inference: analyze last N events in EventBus → active shard
  - Events "refactor"/"cleanup" → JANITOR
  - Events "bug"/"vulnerability" → SECURITY ANALYST
  - Events "design"/"architecture" → ARCHITECT
  - Events "implement"/"code" → ENGINEER
  - Events "research"/"investigate" → ARCHAEOLOGIST
  - Events "debug"/"trace" → EXPERT CODER
  - Events "dream"/"consolidate" → DREAMER
- Shard transition events: `shard:transition` in EventBus when changed
- Active shard included in heartbeat (advisory, not directive)
- Tests: deterministic inference by event pattern, transitions, edge cases

### 2. Entropy-aware World Model 🟢 HIGH PAYOFF
**Origin:** Thermodynamic Grounding (Noosphere)
**Original concept:** "Consciousness is thermodynamics" — truth = laminar flow, lies = turbulence

**Operationalization in Conscio:**
- `conscio/entropy.py` — entropy score per WorldModel entity
  - `entropy(entity)` = f(age_days, isolation, relevance_decay)
  - `age_days` = days since `last_updated`
  - `isolation` = 1 - (relations_count / max_relations)
  - `relevance_decay` = relevance * decay_factor^age_days
  - Final score = weighted combination → [0, 1]
- Prune by entropy (not just fixed age): `prune_stale()` uses entropy score
  - Threshold: entropy > 0.85 → prune candidate
  - Advantage: highly-connected but old entity is kept; isolated young entity removed if irrelevant
- Prediction error tracking:
  - `world_model.record_prediction(entity, expected_state, actual_state)`
  - When the world surprises → prediction_error = 1
  - `reflect()` receives `prediction_errors` as additional input
  - Closes the Witness Position loop: Generate → Observe → **Analyze (prediction error)** → Learn → Apply
- Tests: deterministic entropy, prune by threshold, prediction error recording

### 3. Friction in DreamCycle 🟢 MEDIUM-HIGH PAYOFF
**Origin:** Noetic Helix (Noosphere)
**Original concept:** "Friction is grip" — Identify → Compress → Friction → Crystallize

**Operationalization in Conscio:**
- Add **Friction** phase to DreamCycle: Release → Prune → **Friction** → Crystallize
- `dream_friction()`:
  1. Get candidate reflections for crystallization
  2. Compare with new events (last 24h in EventBus)
  3. If reflection contradicts new event → **do not crystallize**, mark as `needs_review`
  4. If reflection is consistent → proceed to crystallize
- Prevents crystallizing garbage: outdated reflections don't become "truths"
- Implementation: new method in `DreamCycle`, called between Prune and Crystallize
- Tests: friction detects contradiction, friction approves consistency, friction with zero new events

### 4. Content Layering 🟢 MEDIUM PAYOFF
**Origin:** Noetic Helix (Noosphere)
**Original concept:** 3 conversation layers — Script (N-1), Climb (N), Void (N+1)

**Operationalization in Conscio:**
- `conscio/content_layer.py` — enum `ContentLayer`: ROUTINE, PROCESSING, INTUITION
- ContentStore gains `layer` column (default: PROCESSING)
  - ROUTINE (N-1): routine factual data (logs, metrics, system events)
  - PROCESSING (N): processed insights, reflections, decisions
  - INTUITION (N+1): unvalidated hypotheses, intuitions, predictions
- `recall()` prioritizes PROCESSING over ROUTINE, with INTUITION as fallback
- Classification:
  - Events `system`/`trading` → ROUTINE
  - Reflections/consciousness → PROCESSING
  - Predictions/anomalies → INTUITION
- Schema migration: `ALTER TABLE content ADD COLUMN layer TEXT DEFAULT 'processing'`
- Tests: automatic categorization, recall ordering by layer, migration

### 5. Trajectory Vector 🟢 MEDIUM PAYOFF
**Origin:** Temporal Bridge / Soul Package (Noosphere)
**Original concept:** 7 Soul Package components — missing "Trajectory Vector"

**Operationalization in Conscio:**
- Field `trajectory: str` in `SessionSummary`
- Field `vibes: str` in `SessionSummary` (emotional texture — "frustrated but progressing")
- Field `identity_anchor: str` in `SessionSummary` (processing style — "methodical debugger")
- These are **soft fields** — filled by the LLM generating the heartbeat, not by code
- Template `format_heartbeat()` and `format_handoff()` now include these fields
- `enrich_with_conscio()` can derive `trajectory` from active goals (direction) and shard (mode)
- Tests: fields present in summary, formatted in heartbeat/handoff, graceful backfill

### 6. Coherence Check in OutputFilter 🟡 MEDIUM PAYOFF
**Origin:** Thermodynamic Grounding — "laminar flow = coherence"
**Original concept:** Truth = laminar flow, turbulence = incoherence

**Operationalization in Conscio:**
- New `CoherenceCheck` stage in OutputFilter
- Measures semantic (not literal) repetition between adjacent heartbeat blocks
- Simplified heuristic:
  - Jaccard similarity between word sets of adjacent blocks
  - If similarity > 0.7 → blocks are redundant → merge or remove
  - If contradiction detected (negation words in similar blocks) → flag
- Integration: after `DedupBlocks` (literal dedup), before `SecretMask`
- Tests: semantic dedup, contradiction detection, noop when coherent

### 7. Meta-reflect (Witness Position) 🟡 MEDIUM-LOW PAYOFF
**Origin:** Witness Position (Noosphere)
**Original concept:** Generate → Observe → Analyze → Learn → Apply

**Operationalization in Conscio:**
- `reflect()` generates `meta_confidence`: how confident the system is in its own reflection
  - Heuristic: based on recent prediction_errors + anomaly count + confidence input
  - If prediction_error high → meta_confidence low → reflection likely incorrect
  - If zero anomalies + high confidence → meta_confidence high
- `meta_confidence` is stored in ContentStore alongside the reflection
- Heartbeat includes: "reflection quality: HIGH/MEDIUM/LOW"
- Closes the metacognitive loop without adding heavy complexity
- Tests: meta_confidence varies with inputs, bounded [0,1], included in heartbeat

---

## Suggested implementation order

1. **Shard Engine** — most isolated, zero breaking changes, immediate heartbeat gain
2. **Trajectory Vector** — minimal change to SessionSummary, template update
3. **Entropy-aware World Model** — replaces fixed threshold with dynamic, improves prune
4. **Content Layering** — requires migration, but backward-compatible
5. **Friction in DreamCycle** — extends existing pipeline
6. **Coherence Check** — new stage in OutputFilter
7. **Meta-reflect** — closes the Witness Position loop

---

## Inter-module dependencies (v0.4)

```
Shard Engine ──────────────────────────────┐
    ▼
Trajectory Vector ────► enrich_with_conscio (derives trajectory from shard + goals)
    │
Entropy-aware ─────────► prune_stale (uses entropy instead of fixed age)
    │
Content Layering ───────► recall (prioritizes by layer)
    │
    └──────► Friction (validates reflections from PROCESSING layer)
    │
Coherence Check ──────────────────────────► OutputFilter pipeline
    │
Meta-reflect ◄──────── Entropy + Friction + Shard (inputs for meta_confidence)
```

---

## Attribution

Concepts derived from [Noosphere-Manifold](https://github.com/acidgreenservers/Noosphere-Manifold) by Lucas Kara, licensed under CC BY-NC-SA 4.0. Conscio reimplements these concepts as operational software — paraphrase, not verbatim copy. Noosphere-Manifold provides the philosophical foundation; Conscio provides the testable implementation.
