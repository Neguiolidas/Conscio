# Claude Opus 5 — Observed Patterns

Extracted from v3.9.5 bugfix session (commits 0af32ea, BUG-37 and BUG-38).
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
fix(v3.9.5): BUG-38b — expanduser() em 8 variáveis de ambiente restantes

O mesmo defeito do BUG-38 (~ não expandido em HERMES_HOME) existia em 8 outras
variáveis de ambiente. Cada site foi corrigido com .expanduser() na leitura
do Path.

Sites corrigidos:
- session_lifecycle.py:40 (CONSCIO_SESSION_DB)
- session_lifecycle.py:49 (CONSCIO_HANDOFF_DIR)
...

Testes: 35 novos (8 original × 3 modos + 8 extensão isolados + 3 especiais)
Prova vermelha: confirmada — sem fix, ~ literal no path
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

When fixing a schema mismatch (BUG-39), don't change the schema — add a
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
