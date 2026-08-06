# Claude Opus 5 — Observed Patterns

Extracted from v3.9.5–v4.0 sessions (spec review, audit, plan iteration).
These are behavioral patterns observed in Claude's approach to code quality,
debugging, and release management. For adoption by other agents working on
the same codebase.

---

## 1. Surgical Staging

```
git add file1.py file2.py tests/test_thing.py
```

Never `git add -A`. Stage each file explicitly so untracked diagnostic files
(`docs/bugs-*.md`, reports, verdicts) stay out of the commit. The working tree
must be clean after commit — no leftover untracked debris.

**Principle:** The commit is the contract. Everything in it is intentional.
Everything outside it is diagnostic and disposable.

---

## 2. Latch, Don't Force

When a safety latch (`action_lockdown`) persists across restarts, the fix is
**reconciliation** — query the source of truth (CircuitBreaker) on attach and
clear the latch if the condition no longer holds — not `load_state()` blindly
setting it to `False`.

**Pattern:**
- Broken: `state.action_lockdown = False` in `load_state()`
- Correct: `_reconcile_lockdown()` that asks the breaker "are we still in
  lockdown?" and only clears if the answer is no

**Principle:** Safety latches exist for a reason. Reconcile, don't override.

---

## 3. Expand All the Way

When `Path(os.environ.get("HOME_DIR", "~/default"))` is used, the `~` is
literal unless `.expanduser()` is called. The fix must cover **every site**
that reads the variable, not just the one that happened to fail.

**Audit method:**
1. Find the env var name
2. `grep -rn "ENV_VAR_NAME" conscio/` to find every reader
3. Add `.expanduser()` to each `Path()` call
4. Write a test that sets the env var to `~/...` and asserts the resolved path
   does not start with `~`

**Principle:** If one site has the bug, all sites have the bug. Sweep
completely or don't bother.

---

## 4. Test Before You Leave

Every fix gets a dedicated test file. Not a patch to an existing test — a
new file that can be run in isolation. The test must:

1. **Prove the bug exists** (red phase) — temporarily revert the fix and
   confirm the test fails
2. **Prove the fix works** (green phase) — reapply and confirm pass
3. **Cover all variants** — if the bug affects 6 sites, test all 6

**Principle:** A fix without a test is a confession that you don't know if
it works.

---

## 5. Leave a Trail

When stopping work, leave explicit notes about what remains:

```
Two things that remain open and that I did not touch:
1. The 7 other env vars with the same ~ defect
2. The ~/.conscio/live directory that a smoke test created
```

**Principle:** The next agent picks up where you left off. Make the handoff
explicit — not hopeful.

---

## 6. Don't Push Until Asked

Commit locally. Don't push. Don't tag. The version bump is in source, the
CHANGELOG is dated, the commit message is clean. But the ship decision belongs
to the operator.

**Principle:** Commits are cheap and reversible. Pushes are not.

---

## 7. Commit Messages as Documentation

```
fix(v3.9.5): BUG-38b — expanduser() on 8 remaining env vars

The same defect from BUG-38 (~ not expanded in HERMES_HOME) existed in 8 other
env vars. Each site was fixed with .expanduser() on the Path() call.

Fixed sites:
- session_lifecycle.py:40 (CONSCIO_SESSION_DB)
- session_lifecycle.py:49 (CONSCIO_HANDOFF_DIR)
...

Tests: 35 new (8 originals × 3 modes + 8 isolated extensions + 3 special)
Red proof: confirmed — without fix, ~ literal in path
```

**Structure:**
1. One-line summary with scope and bug ID
2. Context paragraph (what was broken, why)
3. Enumerated list of changed sites
4. Test count and red/green evidence

**Principle:** The commit message is the first thing a reviewer reads. Make
it the last thing they need.

---

## 8. Self-Review Before Execution

Before running any plan, audit it against the actual codebase:

- Do the function signatures match?
- Are the constant names correct?
- Does the test fixture match the real API?

Three bugs were found in the plan *before* any code was written, because the
plan was cross-referenced against `engine.py`, `event_bus.py`, and
`context_manager.py`.

**Principle:** Plans are hypotheses. Validate them against the source before
acting.

---

## 9. Preserve Backwards Compatibility

When fixing a schema mismatch, don't change the schema — add a
normalization layer at the boundary:

```
_normalize_event() maps data → payload, defaults source and category
```

Canonical events pass through unchanged. Host-aliased events get mapped.
Neither side breaks.

**Principle:** Fix the seam, not the contract.

---

## 10. Explicit Is Better Than Implicit

- `git add file1.py file2.py` > `git add -A`
- `_reconcile_lockdown()` > silent `False` in `load_state()`
- `.expanduser()` on every `Path(env)` > hoping the env var is absolute
- Staging file-by-file > blanket staging

**Principle:** If you can point to the line that made the decision, it's
explicit. If you have to infer it, it's a bug waiting to happen.

---

## 11. Cross-Validate the Reviewer, Not Just the Author

During the v4.0 spec review, the author claimed `society` was a phantom tool
not in `BASE_TOOL_DEFS`. This was confirmed by grepping the codebase. But the
reviewer went further and **ran the regex from the plan against the real file
tree** — finding that `conscio.remember` and `conscio.timeutil` would also
trigger the phantom-tool scan as false positives. Two distinct categories:

- `conscio.society` → true phantom
- `conscio.remember` → soon-to-be-valid (Task 2 creates the tool)
- `conscio.timeutil` → module reference, not tool

**Principle:** When a spec or plan claims category separation, reproduce the
scan that proves it. Words like "only" and "all" in a spec are hypotheses —
the entity that runs numerically the exact regex against the real file tree
is the one that knows their truth-value.

---

## 12. Trace Back to the Real State, Not the Assumed One

The plan built `resolve_mode` and `LITE_TOOLS`. When adjusting the set, the
original reasoning cited "not breaking existing `--lite` users." But this was
a phantom risk — `lite` was released in v3.9.9 and v3.9.7 was the last real
release, so **no human** has ever used that mode. The plan was updated to
state that, that the set is entirely free of retro-compatibility obligations.

**Principle:** Defects are pre-existing. Features that were never shipped
are pre-existing too — you cannot break a user who never existed. Verify the
last real release before claiming retro-compatibility.

---

## 13. Closed-Set Fail-Closed Over Open-Set Fail-Open

The plan battles the `conscio.<name>` ambiguity (tools vs modules) with
`MODULE_REFS = {"conscio.timeutil"}`— an explicit hardcoded allowlist for
module references. The open-source reflex would be `importlib_utils.find_spec`;
any module name that existed would pass the scan. By being manual, the
reference forces a human to decide the namespace, which is exactly the anti-
stop property of the vendored obsstore copy task.

**Principle:** When a false negative in a safety scan costs a bad deploy,
choose fail-closed (rejected until a human reviews the metadata is a module,
not a tool).

---

## 14. Phantom Risk Over Erroneous Reasoning Preserves Correct Decision Even If Justification Was Wrong

The spec defends `category="consciousness"` for `remember` by saying it maps
to `ContentLayer.PROCESSING`— the highest priority in recall desempate. This
reasoning is unfalsifiable by construction: `layer_of()` has a fallback
To `PROCESSING`, so any unknown category would get the same priority as the
correct one. The correct defense is different: use `.recall(categories=["consciousness"])`
to prove the literal string was stored and filterable — not to prove priority.

On review, the conclusion (consciousness is correct) survives even though
the rationale (priority) was wrong. The fix (one extra assert in Task 2's
round-trip test, filtering by category after restart) validates the
correct reason.

**Principle:** If the selection is solid, fix the justification, not the constraint — and add the test that proves the actual value property, not the one the original author believed.