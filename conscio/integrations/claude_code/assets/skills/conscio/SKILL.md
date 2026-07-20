---
name: conscio
description: Use when a task needs project memory, prior decisions, agent
  society/relay, or consciousness state — pull Conscio context proactively
  (recall at start, remember on closing a decision) via the conscio.* MCP tools.
---

# Conscio (native integration)

Conscio gives this session a persistent mind: episodic memory, a coherence/goal
state, and a society of peer agents. Two faces:

- **Manual:** the `/conscio:*` slash commands (recall, remember, state, society,
  relay, reflect, propose, handoff, awake, sleep).
- **Automatic (prefer this):** reach for the `conscio.*` MCP tools yourself when
  the moment calls for it — don't wait to be told.

## When to act automatically
- **Start of a non-trivial task** → `conscio.recall` the topic before planning.
- **A decision/fact is settled** → `conscio.remember` it (durable only).
- **Coordinating with another agent** → `conscio.relay_send` / check society.
- **Unsure of project direction** → `conscio.state` for goals/coherence.
- **Making an architecture decision** → `conscio.decide` to create an ADR.
- **Need multi-perspective analysis** → `conscio.council` for 3-voice review.
- **Starting an autonomous loop** → `conscio.loop_gate` to verify conditions.
- **Closing a session** → `conscio.delivery_check` runs automatically.
- **Before acting on a target** → `conscio.investigate` to verify you read it.
- **Defining success criteria** → `conscio.acceptance_criteria` for the goal.
- **Verifying work is done** → `conscio.verify` against acceptance criteria.
- **Context getting long** → `conscio.strategic_compact` for compaction advice.
- **Recording a decision** → `conscio.ledger` with coherence marks.
- **Auditing token usage** → `conscio.context_budget` for pressure analysis.
- **Measuring reliability** → `conscio.eval_harness` with pass@k metrics.
- **Finding repeated patterns** → `conscio.rules_distill` to scan and distill.

Keep it light: recall/remember are cheap; don't narrate the tool use, just fold
the result into your work.
