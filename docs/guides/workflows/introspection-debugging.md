# Introspection Debugging Workflow

**Origin:** ECC `agent-introspection-debugging` skill
**Conscio mapping:** `conscio.note` + `conscio.feed` + `conscio.cognitive_cycle`
**Type:** Workflow (sequence of existing MCP tools — no new tools needed)

## When to use

When you're stuck in a loop, getting wrong results, or the agent's behavior doesn't match intent. This is the "I'm broken, let me think" protocol.

## The 4-phase loop

### Phase 1: Failure Capture

**Goal:** Record what went wrong before context is lost.

Call `conscio.note` with the failure:

```json
{
  "type": "introspection:failure",
  "category": "consciousness",
  "data": {
    "what_happened": "Attempted to parse YAML but got JSON",
    "expected": "YAML output from the API",
    "actual": "JSON output with different schema",
    "context": "Calling /api/config endpoint at step 5 of deployment"
  }
}
```

**Rule:** Capture the observable failure, not your diagnosis. "Got JSON instead of YAML" — not "the API is broken."

### Phase 2: Root-Cause Diagnosis

**Goal:** Identify the single most likely cause. Not 5 hypotheses — ONE.

Call `conscio.feed` with the failure event + current world state:

```json
{
  "type": "introspection:diagnosis",
  "category": "consciousness",
  "data": {
    "world_state": "Deploying to staging, API returned unexpected format",
    "recent_events": ["API call succeeded (200)", "Response was JSON, not YAML"]
  }
}
```

Feed triggers reflect automatically. The reflection output will contain:
- Updated confidence (dropped from the failure)
- Anomaly detection (the format mismatch)
- Goal adjustment (new goal: fix API parsing)

**Diagnosis heuristics** (priority order):
1. Did I state the objective correctly? → Restate and retry
2. Does the world match my model? → Verify world state via `conscio.recall`
3. Am I trying to do too much? → Shrink scope to one variable
4. Can I discriminate with one check? → Add a single assertion
5. Only then → Change approach

**Anti-patterns:**
- Retrying the same action with different wording (that's not debugging, that's hoping)
- Adding complexity to work around the symptom instead of fixing the cause
- Blaming the environment without evidence

### Phase 3: Contained Recovery

**Goal:** Fix exactly one thing. Verify it works. Don't touch anything else.

Call `conscio.cognitive_cycle` with the recovery plan:

```json
{
  "world_state": "API confirmed to return JSON; need to update parser",
  "session_tokens": 5000
}
```

The cognitive cycle will:
1. Perceive the updated world state
2. Reflect on the diagnosis
3. Synthesize a recovery plan
4. Propose (or execute) the fix
5. Learn from the correction

**Recovery constraint:** Change ONE variable per iteration. If the fix doesn't work, revert and try the next hypothesis.

### Phase 4: Introspection Report

**Goal:** Record what you learned so you don't repeat the failure.

Call `conscio.note` with the resolution:

```json
{
  "type": "introspection:report",
  "category": "consciousness",
  "data": {
    "root_cause": "API changed format from YAML to JSON without version bump",
    "fix": "Updated parser to accept both formats with auto-detection",
    "prevention": "Add format assertion to API integration tests",
    "confidence_before": 0.8,
    "confidence_after": 0.6
  }
}
```

**After the loop:** Check `conscio.evaluate(task_description="debugging session")` to see if the overall session quality is acceptable.

## Quick reference

```
stuck → note(failure) → feed(diagnosis) → cognitive_cycle(recovery) → note(report)
```

## Conscio tools used

| Tool | Phase | Purpose |
|------|-------|---------|
| `conscio.note` | 1, 4 | Capture failure, record resolution |
| `conscio.feed` | 2 | Perceive + reflect on failure (auto-triggers reflection) |
| `conscio.recall` | 2 | Retrieve past similar failures |
| `conscio.cognitive_cycle` | 3 | Full perceive→reflect→synthesize→propose→learn cycle |
| `conscio.evaluate` | After | Score the debugging session quality |
