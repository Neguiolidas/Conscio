---
name: conscio
description: Use when a task needs project memory, prior decisions, or
  consciousness state — pull Conscio context proactively (recall at start,
  remember on closing a decision) via the conscio.* MCP tools.
---

# Conscio (native integration)

Conscio gives this session a persistent mind: episodic memory, a coherence/goal
state, and structural knowledge of the project. Two faces:

- **Manual:** the `/conscio:*` slash commands — recall, remember, state,
  reflect, propose, handoff, govern, mode, capture, backup, and the four that
  need the full stack (society, relay, awake, sleep).
- **Automatic (prefer this):** reach for the `conscio.*` MCP tools yourself when
  the moment calls for it — don't wait to be told.

## When to act automatically
- **Start of a non-trivial task** → `conscio.recall` the topic before planning.
- **A decision/fact is settled** → `conscio.remember` it (durable only).
- **Unsure of project direction** → `conscio.state` for goals/coherence.
- **Making an architecture decision** → `conscio.decide` to create an ADR.
- **Need multi-perspective analysis** → `conscio.council` for 4-voice review.
- **Verifying work is done** → `conscio.verify` against acceptance criteria.
- **Context getting long** → `conscio.context_budget` for pressure analysis.
- **Handing the session over** → `conscio.handoff` for a successor's briefing.
- **Looking for how the code fits together** → `conscio.kg_query`.
- **Looking for something said earlier this session** →
  `conscio.recall_observations`.

## Tool surface
The list above is the default (`balanced`) surface. `/conscio:mode lite` cuts it
to nine tools when context is tight; `/conscio:mode ultra` exposes everything
this server was started with, including the council's deeper instruments. If a
tool named here is not in your list, you are in `lite` — switch modes rather
than assuming it doesn't exist.

Keep it light: recall/remember are cheap; don't narrate the tool use, just fold
the result into your work.
