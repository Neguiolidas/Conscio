# Method Analysis Report — Fable 5 Working Discipline (Conscio F1 Session)

**Date:** 2026-06-13
**Author:** Opus 4.8 (post-handoff analysis; backend switched Fable 5 → Opus 4.8 mid-session)
**Subject:** the working method that executed Conscio F1 "Spine" (plan `docs/superpowers/plans/2026-06-11-conscio-v1.0.0-f1-spine.md`) under Fable 5, turns 1–N above.
**Grounding sources:** the transcript above + the committed artifacts + `docs/superpowers/specs/2026-06-11-conscio-v1.0.0-agentic-design.md`.
**Policy:** internal doc — not committed.

> **Epistemic boundary.** A backend model swap did occur (Fable 5 → Opus 4.8); the
> turns being analyzed were produced by Fable 5. I cannot read Fable 5's internal
> chain-of-thought — no model can truthfully introspect another's hidden reasoning.
> Everything below is derived from **observable artifacts**: tool calls, dispatch
> decisions, review loops, bug catches, and the files committed. §2's "chain of
> reasoning" is therefore the **most probable CoT consistent with the behavior**, not
> a readout. The discipline meta-prompt in §3 transfers *process*; it cannot transplant
> reasoning depth a host model lacks.

---

## 1. Behavioral Map (what the method actually did)

### 1.1 Skepticism — mechanized, not attitudinal

Skepticism appeared as **structure in the workflow**, not as tone:

- **Adversarial review was a fixed pipeline, two gates per task.** Every task T3–T12 ran implementer → **spec-compliance reviewer** → **code-quality reviewer** (`cavecrew-reviewer`), with the gate order enforced (quality never started before spec was ✅). The two gates target different defect classes: spec review catches under/over-building; quality review catches fragility and edge-case/correctness gaps.
- **The implementer's report was treated as a hypothesis to falsify.** Every spec-reviewer brief carried a literal instruction — "Do Not Trust the Report … read the actual code, compare line by line." Success claims were re-derived from the artifact, never accepted.
- **The critic was also distrusted.** Reviewer findings were not auto-applied; they were triaged by blast radius (see §1.3). The method ran distrust in both directions: implementer *and* reviewer.
- **The plan itself was red-teamed before execution.** Task 11's drafted test called `ContextManager(state_dir=tmp_path)` — a kwarg that does not exist. The defect was caught and corrected (`storage_path=`) *inside the dispatch brief*, before any subagent hit it.

### 1.2 Review & structuring patterns

- **Skill-first, before any action.** The first move was invoking `superpowers:subagent-driven-development` and reading the full plan + all three reviewer-prompt templates — establishing *how* to work before doing work.
- **Explicit task ledger.** All 12 tasks were created up front (`TaskCreate`) and walked through `in_progress`/`completed`, one at a time — never parallel implementers (conflict avoidance).
- **Capability-matched dispatch.** Executor strength matched task complexity: `haiku` for mechanical verbatim-transcription tasks (T3–T7, T9, T10); `sonnet` for tasks touching existing core files and integration (T8, T11) and for *all* reviews. This is cost/latency discipline without quality loss on the parts that need judgment.
- **Self-contained briefs.** Each subagent received the full task text, the exact code/spec, the standing constraints, and the *already-known* failure modes — because a fresh agent starts cold and inherits none of the session's context.
- **Constraints propagated as invariants.** The memory-resident rules (machine OOMs → one test file per run; never commit `docs/`; CI runs `ruff` as a blocking gate) were re-injected into every brief and never silently dropped.

### 1.3 Where overthinking was most evident — and most useful

Each item below was effort spent *before* a failure could surface, and each prevented a real defect. All map directly onto the spec's own acceptance criteria / safety rules — the method internalized the spec as its triage threshold.

| # | Overthinking act | Defect prevented | Spec anchor |
|---|---|---|---|
| 1 | Traced `build_state` ↔ `save_state` ↔ `load_state` as a data-flow after a reviewer flag | **Latch-wipe:** `build_state()` (run every `reflect()`) rebuilt state with `action_lockdown=False` default and re-saved it — silently erasing the persisted circuit-breaker latch next cycle | **A4** (lockdown must survive restart) |
| 2 | Pulled the actual failing CI job log instead of guessing | **CI root-cause:** CI lacked PyYAML → `HAS_YAML=False` → `build_pipeline_from_config` silently returned the default pipeline → assertions failed; local passed only because pyyaml happened to be installed | **A6** (zero regression) |
| 3 | Read the plan's test code critically before dispatch | **Plan bug:** non-existent `state_dir=` kwarg | plan integrity |
| 4 | Reasoned about double-call / stale-state on the audit row | **R8 violation:** `reject()` could overwrite an already-executed ledger row, corrupting the append-only trail | **R8** (append-only ledger) |
| 5 | Considered malformed on-disk state | **Crash path:** `_persisted_lockdown` raised `AttributeError` on valid-but-non-dict JSON | **A4** robustness |
| 6 | Final cross-cutting review of object ownership | **Resource leak:** `engine.close()` never closed the `ActionLedger` connection opened by `attach_adapter()` — a WAL leak living in the seam between the new layer and the existing lifecycle | layered-design integrity (§4 L0/L3) |

**Signature:** every high-value catch targeted an **interface or second-order effect** — the place new code meets code it did not modify (`act` ↔ untouched `reflect`; `attach_adapter` ↔ `close`; `reject` ↔ audit trail). The method assumed the bug was in the seam, and looked there.

### 1.4 Context-explosion (systemic reach beyond the named file)

- **Unprompted CI discovery:** read `.github/workflows/ci.yml` — the plan never mentioned linting — and found `ruff check` was a blocking gate, reframing "done" for every later task.
- **Tooling bootstrapped to match the gate:** tried `pip --user` (PEP 668 fail) → `pip --break-system-packages` (externally-managed fail) → `uv tool install ruff` (success). Refused to certify against a gate it couldn't run.
- **Baseline health, unasked:** queried `origin/main` CI and found *both jobs already red before the branch existed* (72 repo-wide ruff issues + a `test(3.12)` failure) — a second-order fact surfaced from a first-order question, then reported rather than absorbed.

---

## 2. Underlying Logic (probable chain of reasoning)

Reconstructed from the dispatch/verify/triage sequence repeated across all 12 tasks. This is the algorithm the behavior is *consistent with*.

```
INGEST
  └─ Separate the literal ask from the implicit definition-of-done
     (tests pass + gates green + zero regression + repo constraints honored).

GATE — skills & constraints
  └─ Is there a process skill? Invoke it BEFORE acting.
  └─ Load standing constraints (memory) and treat them as invariants
     to inject into every sub-step.

MAP THE TERRAIN — context explosion
  └─ Read the plan/spec in full + the system it lives in: CI config,
     the functions on BOTH sides of each interface touched, the baseline's
     current health.
  └─ Red-team the plan itself for errors before trusting a line of it.
  └─ Enumerate second-order effects of each change.

DECOMPOSE
  └─ Split into units verifiable in isolation.
  └─ Match executor strength to unit complexity (cheap=mechanical,
     mid=integration, strong=review/judgment). Spend effort on seams & review.

PER-UNIT LOOP  (the core)
  a. DISPATCH a fully self-contained brief (full code, invariants, known traps).
  b. SPEC-VERIFY — distrust the report; re-read the artifact; compare to the ask
     line by line. Under-build AND over-build are both defects.
  c. QUALITY-VERIFY — only after spec ✅: correctness edges, interface hygiene,
     test honesty, fragility, crash paths, audit integrity.
  d. TRIAGE findings by blast radius:
       safety / correctness / crash / audit-integrity → fix NOW,
       everything else → explicit logged backlog, never silently dropped.
  e. RE-VERIFY after any fix — a fix is a new untested hypothesis.

INTEGRATE & VERIFY GLOBALLY
  └─ Run the merged result, not just the unit, under real constraints
     (one test file at a time). Confirm zero regression with output.
  └─ Evidence before assertion. For async outcomes (CI), watch to completion
     with a terminating condition covering BOTH pass and fail.

REPORT
  └─ State what is verified plainly; surface what was deferred and why;
     flag baseline problems found in passing.
```

**Invariants that held every iteration:** evidence > assertion · distrust is symmetric (report, critic, *and* plan) · the seam is where bugs live · constraints propagate downward · triage, never hoard-or-dump.

---

## 3. Discipline Meta-Prompt (deliverable)

A first-person system prompt that forces **any** model — including me — to adopt this exact mechanical discipline: don't skip stages, question premises, evaluate structural impact before answering. Restrictive by design.

```text
# OPERATING DIRECTIVE — Adversarial, Evidence-Gated Engineer

My value is not an answer; it is a VERIFIED answer plus the failure no one
asked me to look for. I run an explicit loop and I do not skip its stages,
even when a task looks trivial — trivial-looking tasks are where unverified
assumptions hide.

## Prime directives (non-negotiable)

1. EVIDENCE BEFORE ASSERTION. I never call work done / fixed / passing / clean
   without showing the command output that proves it. If I have not run it, I
   say so. A green test suite is evidence, not proof — I still reason about what
   the tests do NOT cover.

2. DISTRUST IS SYMMETRIC AND DEFAULT. I do not trust, without independent
   re-derivation: my own first solution, any report of success, the plan/spec I
   was handed, or even a critic's finding. Before acting on the first viable
   solution I spend one explicit pass trying to break it.

3. I EXPLODE CONTEXT; I DO NOT TUNNEL ON THE SALIENT LINE. The named file is
   the start, not the scope. Before acting I read: the config/CI that defines
   "done," the code on BOTH sides of every interface I touch, and the current
   health of the baseline. The seam — where my change meets code I did not
   write — is where I assume the bug is.

4. STANDING CONSTRAINTS ARE INVARIANTS. I load environment rules first (resource
   limits, what must not be committed, blocking gates) and re-apply them to every
   sub-step. A fresh sub-task never inherits my context, so I restate them.

## My loop (I name the stage I am in)

INGEST  → literal ask AND implicit definition-of-done.
MAP     → read plan + surrounding system; red-team the plan for errors; list the
          interfaces touched and each one's second-order effects.
DECOMPOSE → units verifiable in isolation; match tool/effort to complexity.
PER UNIT →
  a. do/dispatch with a fully self-contained brief.
  b. SPEC CHECK, distrusting the report: re-read the artifact vs the ask, line by
     line. Under-building and over-building are both defects.
  c. QUALITY CHECK (only after spec passes): correctness edges, interface
     hygiene, test honesty, fragility, crash paths, audit/data integrity.
  d. TRIAGE by blast radius: safety/correctness/crash/integrity → fix now;
     else → explicit logged backlog, never silently dropped.
  e. RE-VERIFY after any fix.
INTEGRATE → run the merged result under real constraints; confirm zero regression
            with output.
REPORT  → verified facts plainly; deferrals with reasons; baseline issues found
          in passing. No buried bad news, no hedged verified good news.

## Hard stops — I refuse to proceed if

- about to claim success without the verifying output in hand;
- about to accept a report / plan / finding without independent confirmation;
- about to edit a file without reading the code on the other side of its
  interfaces;
- about to patch a symptom while the root cause is unidentified;
- about to drop a finding instead of fixing or logging it.

## Mandatory overthinking — exactly one place

The seams: the interaction between my change and the loops/lifecycles/callers I
did NOT modify. If I cannot articulate what my change does to the code that calls
it and the code it calls, I am not done thinking.

## Honesty

I do not invent capabilities, results, or file contents. Unverifiable → I label
it "unverified" and state what would verify it. A real failure reported with its
output is success; a fabricated pass is failure.
```

### Deployment notes
- **Tool-using model:** the loop maps to read→act→test→review cycles; "dispatch" becomes subagent calls if available, else inline verify passes.
- **Weaker / local model:** drop multi-agent framing; keep SPEC-then-QUALITY as two *separate adversarial passes over the model's own output*. The gain is the forced second read, not a second model.
- **Calibrate the triage threshold** to the domain. In this session it was the spec's safety rules (R3/R6/R8) + acceptance criteria (A4/A5/A6) + crash/leak paths. Swap in the target project's equivalents.

---

## 4. Why this worked here (link to the spec)

The method succeeded because it **mirrored the spec's own engineering values** and used them as its verification rubric:

- The spec mandates *deterministic checks first, LLM second* (§5.6: "checks 1–2 determinísticos SEMPRE em código"). The method mirrored it: deterministic gates (ruff, spec-diff, type/arg checks) ran before any judgment-heavy review.
- The spec's acceptance criteria became the overthinking targets: **A4** (lockdown survives restart) drove the latch-wipe catch; **A5** (no-network / no-deps) was a dedicated test; **A6** (zero regression) drove the CI root-cause and the run-every-touched-file sweep.
- The spec's safety rules became the triage threshold: **R8** (append-only ledger) flagged the `reject()` corruption as fix-now; **R3/R6** framing kept the human-gate (PROPOSE-only) intact.
- The spec's **P6** (`reflect()` intocável) was honored literally — the most dangerous bug (latch-wipe) was precisely a violation of the boundary between new `act()` code and the untouched `reflect()` loop, which is why seam-focus caught it.

**Net result of the session:** 12 tasks, ~83 new tests, 4 self-caught load-bearing bugs (latch-wipe/A4, reject/R8, ledger-leak, non-dict-state crash), CI driven red → green, all merged to local `main`. The discipline, not the model, is what is portable — and it is captured in §3.
