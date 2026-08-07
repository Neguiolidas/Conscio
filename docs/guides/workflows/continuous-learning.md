# Continuous Learning Workflow

**Origin:** ECC `continuous-learning-v2` skill
**Conscio mapping:** `conscio_feed` + `conscio_note` + `conscio_recall`
**Type:** Workflow (capture → generalize → persist → reuse)

> **v3.0 enrichment:** `conscio_acceptance_criteria` defines what "learned" means;
> `conscio_verify` confirms the pattern applies; `conscio_rules_distill` automates
> the generalize step.

## When to use

When you discover a non-trivial pattern, workaround, or insight that you'll need again. This is the "write it down before you forget" protocol.

## The 4-step loop

### Step 1: Discover

While working, you notice something non-obvious:
- A bug that required a non-standard fix
- A pattern that keeps recurring
- A constraint that isn't documented anywhere
- A shortcut that saved significant time

**Trigger:** "Hmm, I didn't know that" or "I've seen this before."

### Step 2: Capture

Call `conscio_note` with the discovery:

```json
{
  "type": "learning",
  "category": "knowledge",
  "data": {
    "pattern": "SQLite WAL mode requires checkpoint under high write load",
    "context": "Conscio EventBus with >1000 events/minute",
    "fix": "Run PRAGMA wal_checkpoint(TRUNCATE) every 5000 writes",
    "evidence": "Measured 3x write throughput improvement after enabling periodic checkpoints",
    "applicability": "Any SQLite-backed high-write service"
  }
}
```

**Rules:**
- Write the pattern, not the incident. "SQLite WAL checkpoint" — not "that time the DB was slow on Tuesday."
- Include evidence. A pattern without evidence is an opinion.
- State applicability. Who else might need this?

### Step 3: Generalize

Call `conscio_feed` to trigger reflection on the discovery:

```json
{
  "type": "learning:reflect",
  "category": "consciousness",
  "data": {
    "world_state": "Discovered SQLite WAL checkpoint pattern",
    "recent_events": ["Measured write throughput", "Applied checkpoint fix"]
  }
}
```

The reflection will:
- Update confidence in the pattern
- Connect to existing knowledge (via world model)
- Potentially generate a maintenance goal (if the pattern reveals a systematic issue)

### Step 4: Persist and Reuse

**Persist:** The `conscio_note` call already stored it in the EventBus. For long-term storage, the ContentStore captures it via the structural pipeline.

**Reuse:** Next time you encounter a similar situation, call `conscio_recall`:

```json
{
  "query": "SQLite write performance slow",
  "k": 3,
  "categories": ["knowledge"]
}
```

The recall will surface the stored pattern with its evidence.

## Workflow composition

Continuous learning composes with the other two Conscio workflows:

- **After Introspection Debugging:** The Phase 4 report IS a learning — feed it through this workflow to persist the debug insight.
- **After Architecture Audit:** Each finding IS a learning — capture them before they're forgotten.
- **After `conscio_evaluate()`:** If the scorecard reveals a weakness, capture the improvement as a learning.

## Quick reference

```
discover → note(pattern+evidence) → feed(reflect) → recall(future reuse)
```

## Conscio tools used

| Tool | Step | Purpose |
|------|------|---------|
| `conscio_note` | 2 | Capture the pattern with evidence |
| `conscio_feed` | 3 | Reflect and connect to existing knowledge |
| `conscio_recall` | 4 | Retrieve when needed again |
| `conscio_evaluate` | After | Score the learning session quality |
