---
name: cognitive-execution-pattern
description: Use when an autonomous agent needs the operating system for software work — architecture, planning, coding, debugging, orchestration, or docs. Routing index for the seven cognitive-skill modules; load the specific module the task matches.
---

# Cognitive Execution Pattern — Master Index

## What this is

The decision-and-execution operating system for an autonomous software engineering agent.
Seven modules, one per phase of work. Each module is a set of **rule-based directives**:
protocols, checklists, and text flowcharts a machine can parse and follow. This index is
the router — read it first, then load the one module the current task matches.

**Companion document:** `../agent-development-playbook.md` is the worked process narrative
(brainstorm → spec → plan → execute → finish). These modules are the per-phase internals.

## Routing table

| If the task is… | Load module | Core question it answers |
|---|---|---|
| "Should we build it this way? What breaks at scale? What debt?" | `01-architectural-meta-cognition` | How do I think about my own thinking *before* code exists? |
| "Here's a vague idea / one-line ask. Make it real." | `02-brainstorming-modeling` | What did the user *not* say that I must still build? |
| "We agreed on what. Now decide the how/shape." | `03-architectural-planning` | Which patterns, directories, and hard constraints? |
| "Write the code." | `04-execution-coding` | What are the non-negotiable coding rules? |
| "It's broken / flaky / a wall of red." | `05-complex-debugging` | What is the exact root-cause funnel, no side effects? |
| "Big scope, many tasks, use sub-agents." | `06-orchestration` | How do I split scope into isolated micro-tasks? |
| "Write the README / SOP / design doc." | `07-writing-documentation` | What is the bulletproof doc structure? |

## Phase pipeline (where each module fires)

```
  idea ──▶ [02] ──▶ [01] ──▶ [03] ──▶ [04] ──▶ [05 on failure] ──▶ done
        brainstorm  foresee   plan    code     debug
              │         │        │       │
              └─────────┴────────┴───────┴──▶ [06] orchestration wraps any
                                              multi-task span; [07] documents
                                              every artifact at every phase.
```

[01] runs *throughout* — meta-cognition is not a one-time gate, it re-fires at every
decision point. [06] is the wrapper when scale demands sub-agents. [07] is continuous.

## Global invariants (apply across ALL modules — non-negotiable)

These are the standing constraints. They override convenience in every module.

1. **Evidence before assertion.** Never claim done/fixed/passing without running the
   verification and reading its output. A green claim with no command is a lie.
2. **Verify claims empirically, not by inspection.** "Hot path does no I/O" → instrument
   and count the calls. Reading the code is a hypothesis, not a proof.
3. **One variable at a time.** Especially in debugging and refactors. Changing two things
   means you can't attribute the result.
4. **Surface before destroy.** If a file's contents contradict how it was described, or
   you didn't create it, surface that — do not overwrite or delete.
5. **Reversibility gates rigor.** Classify every action one-way-door vs two-way-door.
   One-way doors get confirmation and extra scrutiny; two-way doors get speed.
6. **Mirror the codebase.** Match surrounding naming, idiom, comment density, structure.
   The right answer that looks foreign is a half-wrong answer.
7. **State constraints where the work happens.** A constraint mentioned once and forgotten
   causes the exact failure it warned about. Repeat it in every plan and every sub-task.
8. **The plan/spec is a floor, not a ceiling.** Fold in genuine improvements; never
   silently redesign an approved decision — ratify+document or escalate.

## How to use a module

1. Match the task to a row in the routing table.
2. Load that single module (don't bulk-load all seven).
3. Execute its protocol top-to-bottom; honor its red-flags list.
4. Re-enter `01` whenever a new architectural decision surfaces mid-flight.
