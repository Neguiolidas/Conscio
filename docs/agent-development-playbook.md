# Agent Development Playbook

> How to plan and ship a non-trivial feature with high confidence. Distilled from
> the Conscio v0.8 "Semantic Reconciliation" build (9 tasks, 56 new tests, merged
> clean on the first pass). Transferable to any codebase. The worked example is
> Conscio, but the method is general.

---

## 0. The pipeline

```
brainstorm → spec (design doc) → writing-plans → EXECUTE → finish branch
                                                   │
                                   ┌───────────────┴───────────────┐
                              subagent-driven                     solo
                          (fresh agent + 2-stage             (you implement,
                           review per task)                   inline TDD)
```

Each arrow is a gate. Don't skip a gate because the work "feels simple" — the
gates are where unexamined assumptions die cheaply instead of expensively.

---

## 1. Choose the execution mode

| Use **subagent-driven** when | Use **solo** when |
|---|---|
| Tasks are mostly independent | Tasks are tightly coupled / exploratory |
| Plan is complete (full code per step) | Plan is thin / needs discovery while coding |
| You want isolated context per task + review checkpoints | One short focused change |
| The work is large (many files, many tasks) | A trivial mechanical edit |

Subagent-driven costs more tokens (1 implementer + 2 reviewers per task) but
catches issues early and keeps **your** context clean for coordination. Default
to it for anything ≥ ~4 tasks with a real plan. **Only spawn agents when the user
opted into that scale** — otherwise do it solo.

---

## 2. The plan is the leverage point

A great plan makes execution mechanical. Write it assuming the implementer has
**zero context for the codebase and questionable taste**.

### 2.1 Pre-flight (do this BEFORE writing tasks)

Read **every file the plan will touch** and every pattern it will mirror. This is
non-negotiable and pays for itself:

- **Confirm the real signatures.** Mirror exact method names/shapes — a function
  called `clearLayers()` in one task and `clearFullLayers()` in another is a bug
  you ship blind.
- **Find the tests that will BREAK.** A refactor that changes behavior (e.g. a
  hot-path read becomes a cached read) silently breaks tests that asserted the old
  behavior. Grep for them and write **migration steps** into the plan. (In v0.8,
  `ontological_score` going cache-only broke one `test_coherence.py` assertion —
  caught at plan time, migrated in the task, not discovered at merge.)
- **Verify the patterns you claim to mirror actually exist.** The v0.8 spec said
  "mirror the voice-preset `__init__.py`" — but `presets/voice/` has none. The
  plan corrected this (path-glob loading, no package marker). Don't trust the
  spec's claims about the code; check the code.

### 2.2 Plan structure

```
# <Feature> Implementation Plan
> REQUIRED SUB-SKILL: subagent-driven-development (or executing-plans)
**Goal:** one sentence
**Architecture:** 2-3 sentences
**Tech Stack:** key libs

## Standing constraints (repeat the non-negotiables here)
## Design decisions locked in (deviations from spec + RATIONALE)
## Accepted trade-offs (reviewer caveats folded as ratified, NOT defects)
## File Structure (table: each file → one responsibility)
## Task N: ...
## Self-Review (spec coverage / placeholder scan / type consistency)
```

### 2.3 Task granularity — bite-sized TDD

Each task is a tight loop with **complete code in every step**:

1. Write the failing test (full test code, not "write tests for the above")
2. Run it → expected FAIL message
3. Write minimal implementation (full code)
4. Run it → expected PASS (with count)
5. Run regressions (named files, one process each)
6. Commit (exact message + trailer)

**No placeholders ever.** "TBD", "add error handling", "handle edge cases",
"similar to Task N" are plan failures. If a step changes code, the code is there.

### 2.4 Fold review feedback into the plan as "accepted trade-offs"

When a reviewer (human or agent) raises caveats that are *intended behavior*, not
defects, record them in an **Accepted trade-offs** section with rationale. This
stops a future reader (or implementer) from "fixing" deliberate design. Example
ratified trade-offs from v0.8: a pre-existing blanking contract, a lexical-only
fallback when unwired, hardcoded thresholds (YAGNI), bounded-window self-resolution.

---

## 3. Subagent-driven execution loop

Per task: **implementer → spec-compliance review → code-quality review.** Spec
review FIRST (built the right thing?), then quality (built it well?). Never start
quality review while spec review has open issues.

### 3.1 Implementer prompt anatomy

Give the subagent everything; never make it read the plan file (context isolation
+ efficiency). Structure:

1. **Standing constraints block** — the non-negotiables, verbatim, every time
   (test-running limits, language/tool quirks, commit trailer).
2. **Context / scene-setting** — where this task fits, what already exists, the
   key design points and gotchas (e.g. "preserve X across re-adds", "import Y
   lazily to avoid a cycle").
3. **Full task text** — the TDD steps with complete code, pasted in.
4. **Report format** — Status (DONE / DONE_WITH_CONCERNS / BLOCKED /
   NEEDS_CONTEXT), what changed, exact test counts, commit SHA, self-review.

### 3.2 Handle implementer status honestly

- **DONE** → proceed to spec review.
- **DONE_WITH_CONCERNS** → read the concern; a 4th-file touch that's a legit
  registry-completeness fix is fine, a correctness doubt is not.
- **NEEDS_CONTEXT** → provide it, re-dispatch.
- **BLOCKED** → change something (more context, stronger model, smaller task, or
  escalate to the human). Never retry the same model unchanged.

### 3.3 Track progress durably

Use a task tracker (TodoWrite / Task tools). It survives context compaction across
a long multi-task run and shows the human where you are.

---

## 4. Evaluating reviewer findings — the real skill

**Do not trust the report. Do not auto-apply every finding.** A review is input,
not a verdict. Triage each finding:

| Category | Action |
|---|---|
| **Real bug** (verified against code) | Fix it. |
| **Real gap** (missing test for a load-bearing invariant) | Add the test. |
| **Design-judgment** (defensible either way) | Ratify + document, OR escalate to the human if it changes approved behavior. Don't redesign an approved spec mid-flight. |
| **Nit** (style/DRY/optional) | Fold if cheap & valuable, else skip with a one-line rationale. |
| **False positive** (reviewer misread) | Verify against code, reject with the file:line that disproves it. |

Examples from v0.8:
- *Real bug:* `mark_contradictions` returned an orphan `from`-entity it couldn't
  cache → return ≠ cache. Fixed (skip orphans).
- *Real gap:* the margin guard (the headline precision feature) was never
  exercised because the stub used antipodal vectors. Added an orthogonal-pole test.
- *Design-judgment:* state-log "re-arm" — ratified as bounded-window
  self-resolution (spec-intended), documented + tested rather than redesigned.
- *False positive:* a reviewer claimed `last.dominant.dimension` could
  `AttributeError` — the `if (last and last.dominant)` guard short-circuits. Rejected.

**Fold improvements beyond the plan when they're genuinely better** (utf-8 reads,
ordering-invariant tests, behavior-change docstrings). The plan is a floor, not a
ceiling.

---

## 5. The fix loop

After a reviewer flags issues, the implementer fixes and the reviewer re-checks —
repeat until clean.

- If you can **continue the same implementer agent** (it has full context), do that.
- If you can't (no continue-agent mechanism) and the fix is **trivial/mechanical**
  and you have full context, apply it **inline** — a fresh cold subagent for a
  two-line fix wastes a context rebuild. (You authored the plan; you know the code.)
- If the fix is **substantive** and you lack context, dispatch a fresh fix subagent
  or escalate. Don't pollute your coordination context with deep implementation.

**Commit hygiene:** `git commit --amend --no-edit` to fold review fixes into the
one clean commit per task (local-only; safe when nothing is pushed). One task =
one reviewed commit.

---

## 6. Honor hard constraints like they're load-bearing (they are)

Repeat them in every plan and every subagent prompt. From this project:

- **Memory/RAM:** never run the full test suite — run **one file per process**.
  No parallel (`-n`), no coverage. (The box OOMs; a full run hangs the machine.)
- **Tooling:** `python` is absent → `python3` / `python3 -m pytest`.
- **No git remote** → finish branches by **local merge only**; PR option is dead.
- **Commit trailer** appended to every commit.
- **Surface before destroy** — if a file's contents contradict how it was
  described, or you didn't create it, surface that instead of overwriting.

The point: a constraint stated once and forgotten causes the exact failure it
warned about. State it where the work happens.

---

## 7. Patterns that recur in correct work

- **Preserve regressions.** Every refactor re-runs the tests that depended on the
  old behavior. If behavior changes deliberately, **migrate** the test (don't
  delete/skip it) and assert the new contract.
- **Document deliberate behavior changes** at the function and in the docs, so a
  future reader doesn't "fix" them. (Cold-world → 1.0; negative coherence delta.)
- **Offline-degradable + cheapest-path-first.** Optional heavy deps (embeddings,
  network) behind a probe; a fast deterministic path (lexical) runs first and
  short-circuits. Confine all heavy I/O off the hot path; prove it empirically.
- **Keep return values consistent with persisted state.** If a method returns a
  set and writes a cache, they must agree (the orphan-`from` fix).
- **Verify claims empirically, not by inspection alone.** "Hot path does no
  network I/O" → instrument `__init__`/`reflect()` and count the calls (0).
- **Bounded windows self-resolve.** A flag derived from a capped history ages out;
  document the bound as the resolution mechanism.
- **Deterministic test doubles.** Stub the expensive boundary (the embedder) with
  hand-placed vectors so tests are fast, offline, and exact. But make sure the
  stub doesn't *mask* the behavior under test (antipodal vs orthogonal poles).

---

## 8. Anti-patterns to avoid

- Trusting a subagent's "all green" without reading the diff and re-running.
- Papering over a failing test by editing the test instead of the code.
- Redesigning an approved spec mid-execution over a single reviewer's judgment
  call — ratify + document, or escalate.
- Running the full suite "just to be sure" on a memory-constrained box.
- Letting docs drift from code — re-verify every factual claim (markers, env vars,
  constants, signatures, counts) against the implementation before merge.
- Spawning a fresh agent for a two-line fix you can make in five seconds.
- Auto-merging or discarding a branch without explicit user choice.

---

## 9. Finishing a branch

1. **Verify tests** (RAM-safe sweep) — green before you offer options.
2. **Detect environment** (normal repo vs worktree; base branch; remote?).
3. **Present exactly the options** and let the human choose (merge locally / PR /
   keep / discard). Never auto-merge; require typed confirmation to discard.
4. **Execute** the choice. For a local merge with no remote: `checkout base →
   merge → smoke-test the merged tree → delete the merged branch`.
5. **Update memory** if the project's recorded state drifted (e.g. version bump).

---

## 10. Worked example — Conscio v0.8 in one paragraph

Spec (semantic contradiction by meaning) → plan (9 bite-sized TDD tasks, full
code, breaking-test migration pre-identified, 6 reviewer caveats folded as accepted
trade-offs) → subagent-driven execution (implementer + spec-review + quality-review
per task; every reviewer finding triaged skeptically — real bugs fixed, gaps
covered with new tests, judgment calls ratified+documented, false positives
rejected; fixes folded inline via `--amend` since trivial) → final whole-branch
review (READY TO MERGE, hot-path-network-free verified empirically) → local
fast-forward merge to `main`, smoke test, branch deleted, memory updated. 56 new
tests, 600 total, all green, zero rework after merge.
