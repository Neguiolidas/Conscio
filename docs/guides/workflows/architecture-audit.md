# Architecture Audit Workflow

**Origin:** ECC `agent-architecture-audit` skill (12-layer agent stack)
**Conscio mapping:** `conscio.structure` + `conscio.state` + `conscio.events` + `conscio.evaluate`
**Type:** Workflow (diagnostic checklist using existing MCP tools)

## When to use

When the agent is producing inconsistent results, losing context, hallucinating tool calls, or degrading over time. This is the "systematic health check" protocol.

## The 12-layer agent stack

Every LLM-based agent has these layers. A bug can be in any of them. Audit from bottom to top — fix from bottom to top.

| # | Layer | What it does | Conscio tool to check |
|---|-------|-------------|----------------------|
| 1 | System prompt | Identity, constraints, style | `conscio.state` (mode, metabolic) |
| 2 | Session history | Recent conversation turns | `conscio.events` (recent event log) |
| 3 | Long-term memory | Persisted facts, preferences | `conscio.recall` (FTS5 query) |
| 4 | Distillation | Compressed context from past | `conscio.structure` (structural digest) |
| 5 | Active recall | Working set from memory | `conscio.recall` + `conscio.advisory` |
| 6 | Tool selection | Which tools, in what order | `conscio.state` (pending proposals) |
| 7 | Tool execution | Actual tool calls and results | `conscio.events` (filter: tool_call) |
| 8 | Tool interpretation | How results are understood | `conscio.evaluate` (actionability axis) |
| 9 | Answer shaping | Output formatting, compression | `conscio.state` (output filter config) |
| 10 | Platform rendering | How the user sees it | Manual check (outside Conscio) |
| 11 | Hidden repair loops | Auto-retry, self-correction | `conscio.events` (filter: intercept) |
| 12 | Persistence | What gets saved for next time | `conscio.structure` (drift, freshness) |

## Audit procedure

### Step 1: Baseline

```json
// Get current state
conscio.advisory()

// Get structural health
conscio.structure()

// Get self-evaluation
conscio.evaluate(task_description="architecture audit")
```

Record the overall score and any axes below 4.

### Step 2: Layer-by-layer check

For each layer 1-12, ask:

1. **Is this layer present and active?** (a missing layer is a bug)
2. **Is it producing correct output?** (garbage in → garbage out)
3. **Is it interfering with adjacent layers?** (isolation failure)

**Common failures by layer:**

- **Layers 1-2 (prompt/history):** Context contamination — old instructions leaking into new turns. Check: `conscio.events` for stale directives.
- **Layers 3-5 (memory):** Memory contamination — wrong facts persisting. Check: `conscio.recall` for contradictory entries.
- **Layers 6-8 (tools):** Tool discipline — hallucinating calls, skipping verification. Check: `conscio.events` for tool_call patterns.
- **Layers 9-10 (output):** Rendering corruption — formatting eating content. Check: `conscio.state` for output filter config.
- **Layer 11 (repair):** Hidden loops — auto-retry masking the real error. Check: `conscio.events` for repeated error→retry cycles.
- **Layer 12 (persistence):** State drift — saved state diverging from reality. Check: `conscio.structure` for drift score.

### Step 3: Fix order (code-first, not prompt-first)

When you find issues in multiple layers, fix in this order:

1. Code-gate tool requirements (layer 6-7)
2. Remove hidden repair loops (layer 11)
3. Reduce context duplication (layers 2+4)
4. Tighten memory admission (layer 3)
5. Tighten distillation (layer 4)
6. Reduce rendering mutation (layers 9-10)
7. Typed JSON envelopes (layer 8)

**Why code-first?** Prompt fixes are fragile — they work for one model, break for another. Code fixes are deterministic.

### Step 4: Post-audit evaluation

```json
conscio.evaluate(task_description="architecture audit", output="<audit findings>")
```

Verify that:
- Overall score improved from baseline
- No axis dropped below 3
- Improvements list is actionable

## Quick reference

```
audit → advisory + structure + evaluate → check 12 layers → fix code-first → re-evaluate
```

## Conscio tools used

| Tool | Purpose |
|------|---------|
| `conscio.advisory` | Current cognitive state (baseline) |
| `conscio.state` | Full ConsciousnessState snapshot |
| `conscio.events` | Recent events (filter by type) |
| `conscio.recall` | Retrieve from long-term memory |
| `conscio.structure` | Structural digest, drift, freshness |
| `conscio.evaluate` | Pre/post audit scorecard |
